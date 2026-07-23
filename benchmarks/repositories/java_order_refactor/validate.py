from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GOLDEN_TESTS = 4
SPY_TESTS = 2
COLLABORATOR_SIGNATURES = {
    "OrderValidator": r"\bvoid validate\(Order\);",
    "DiscountPolicy": r"\bint discountedTotal\(Order\);",
    "OrderNotifier": r"\bvoid notifyPlaced\(Order, OrderResult\);",
}

PROBE_STATE_SOURCE = """
public final class ProbeState {
    public static final int CONTROLLED_TOTAL = 4_321;
    public static int validatorCalls;
    public static int discountCalls;
    public static int notifierCalls;
    public static boolean reject;
    public static Order notifiedOrder;
    public static OrderResult notifiedResult;

    private ProbeState() {
    }

    public static void reset() {
        validatorCalls = 0;
        discountCalls = 0;
        notifierCalls = 0;
        reject = false;
        notifiedOrder = null;
        notifiedResult = null;
    }

    public static void validate(Order order) {
        validatorCalls += 1;
        if (reject) {
            throw new IllegalArgumentException("rejected by protected validator spy");
        }
    }

    public static int discountedTotal(Order order) {
        discountCalls += 1;
        return CONTROLLED_TOTAL;
    }

    public static void notifyPlaced(Order order, OrderResult result) {
        notifierCalls += 1;
        notifiedOrder = order;
        notifiedResult = result;
    }

    public static Object invoke(String collaborator, Object[] arguments) {
        if ("OrderValidator".equals(collaborator)) {
            validate((Order) arguments[0]);
            return null;
        }
        if ("DiscountPolicy".equals(collaborator)) {
            return Integer.valueOf(discountedTotal((Order) arguments[0]));
        }
        if ("OrderNotifier".equals(collaborator)) {
            notifyPlaced((Order) arguments[0], (OrderResult) arguments[1]);
            return null;
        }
        throw new IllegalArgumentException("unknown collaborator " + collaborator);
    }
}
""".strip()

PROBE_HARNESS_TEMPLATE = """
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;

public final class OrderServiceDelegationProbe {
    private static final int EXPECTED_TESTS = 2;
    private static int completedTests;

    private OrderServiceDelegationProbe() {
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void installInterfaceSpy(
        OrderService service,
        String collaborator
    ) throws Exception {
        int installed = 0;
        for (Field field : OrderService.class.getDeclaredFields()) {
            Class<?> type = field.getType();
            if (!type.isInterface() || !type.getSimpleName().equals(collaborator)) {
                continue;
            }
            Object proxy = Proxy.newProxyInstance(
                type.getClassLoader(),
                new Class<?>[] {type},
                (instance, method, arguments) -> {
                    if (method.getDeclaringClass() == Object.class) {
                        if ("toString".equals(method.getName())) {
                            return "CodeRadarSpy(" + collaborator + ")";
                        }
                        if ("hashCode".equals(method.getName())) {
                            return Integer.valueOf(System.identityHashCode(instance));
                        }
                        if ("equals".equals(method.getName())) {
                            return Boolean.valueOf(instance == arguments[0]);
                        }
                    }
                    Object[] safeArguments = arguments == null
                        ? new Object[0]
                        : arguments;
                    return ProbeState.invoke(collaborator, safeArguments);
                }
            );
            field.setAccessible(true);
            field.set(service, proxy);
            installed += 1;
        }
        require(installed == 1, collaborator + " must be one injectable field");
    }

    private static void acceptsWhenValidatorSpyAllows(OrderService service) {
        ProbeState.reset();
        Order deliberatelyInvalid = new Order("spy-accept", -7, false, "  ");
        OrderResult result = service.place(deliberatelyInvalid);
        require(ProbeState.validatorCalls == 1, "validator must be called exactly once");
        require(ProbeState.discountCalls == 1, "discount policy must be called exactly once");
        require(ProbeState.notifierCalls == 1, "notifier must be called exactly once");
        require(
            result.finalTotalCents() == ProbeState.CONTROLLED_TOTAL,
            "service must use the discount policy return value"
        );
        require(ProbeState.notifiedOrder == deliberatelyInvalid, "notifier received wrong order");
        require(ProbeState.notifiedResult == result, "notifier received wrong result");
    }

    private static void stopsWhenValidatorSpyRejects(OrderService service) {
        ProbeState.reset();
        ProbeState.reject = true;
        boolean rejected = false;
        try {
            service.place(new Order("spy-reject", 12_000, true, "valid@example.test"));
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        require(rejected, "service must propagate collaborator validation failure");
        require(ProbeState.validatorCalls == 1, "validator must be called exactly once");
        require(ProbeState.discountCalls == 0, "discount must not run after validation failure");
        require(ProbeState.notifierCalls == 0, "notifier must not run after validation failure");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1 || !args[0].matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException("one validator nonce is required");
        }
        RecordingNotificationPort directPort = new RecordingNotificationPort();
        OrderService service = new OrderService(directPort);
__INTERFACE_INSTALLS__
        acceptsWhenValidatorSpyAllows(service);
        require(directPort.count() == 0, "service bypassed OrderNotifier");
        completedTests += 1;
        stopsWhenValidatorSpyRejects(service);
        require(directPort.count() == 0, "invalid order reached NotificationPort directly");
        completedTests += 1;
        require(completedTests == EXPECTED_TESTS, "protected spy test count changed");
        System.out.println(
            "CODERADAR_CHILD_COMPLETE task=bench_014 phase=delegation nonce="
                + args[0]
                + " tests="
                + completedTests
                + " failed=0"
        );
    }
}
""".strip()


def _completion_line(phase: str, nonce: str, tests: int) -> str:
    return (
        f"CODERADAR_CHILD_COMPLETE task=bench_014 phase={phase} "
        f"nonce={nonce} tests={tests} failed=0"
    )


def _run_completed_child(
    java: str,
    classpath: str,
    class_name: str,
    phase: str,
    expected_tests: int,
) -> tuple[bool, str]:
    nonce = secrets.token_hex(16)
    executed = subprocess.run(
        [java, "-ea", "-cp", classpath, class_name, nonce],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    output = (executed.stdout + executed.stderr).strip()
    expected = _completion_line(phase, nonce, expected_tests)
    complete = output.splitlines().count(expected) == 1
    return executed.returncode == 0 and complete, output


def _inspect_contract(
    javap: str,
    build: Path,
    collaborator: str,
    signature: str,
) -> tuple[bool | None, str | None]:
    inspected = subprocess.run(
        [javap, "-classpath", str(build), "-public", collaborator],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    )
    output = (inspected.stdout + inspected.stderr).strip()
    if inspected.returncode:
        return None, f"cannot inspect {collaborator}: {output}"
    if not re.search(signature, output):
        return None, f"{collaborator} does not expose its required public method"
    is_interface = bool(re.search(rf"\binterface\s+{collaborator}\b", output))
    return is_interface, None


def _component_spy_source(name: str, is_interface: bool) -> str:
    declarations = {
        "OrderValidator": "void validate(Order order)",
        "DiscountPolicy": "int discountedTotal(Order order)",
        "OrderNotifier": "void notifyPlaced(Order order, OrderResult result)",
    }
    declaration = declarations[name]
    if is_interface:
        return f"public interface {name} {{ {declaration}; }}\n"
    if name == "OrderValidator":
        body = "ProbeState.validate(order);"
        constructors = "public OrderValidator() {}"
    elif name == "DiscountPolicy":
        body = "return ProbeState.discountedTotal(order);"
        constructors = "public DiscountPolicy() {}"
    else:
        body = "ProbeState.notifyPlaced(order, result);"
        constructors = (
            "public OrderNotifier() {} "
            "public OrderNotifier(NotificationPort ignored) {}"
        )
    return (
        f"public class {name} {{ {constructors} "
        f"public {declaration} {{ {body} }} }}\n"
    )


def _run_delegation_probe(
    javac: str,
    java: str,
    main_build: Path,
    temporary: Path,
    interface_kinds: dict[str, bool],
) -> tuple[bool, str]:
    source_root = temporary / "spy-source"
    spy_build = temporary / "spy-build"
    source_root.mkdir()
    spy_build.mkdir()
    sources: dict[str, str] = {
        "ProbeState.java": PROBE_STATE_SOURCE + "\n",
    }
    installs: list[str] = []
    for collaborator, is_interface in interface_kinds.items():
        sources[f"{collaborator}.java"] = _component_spy_source(
            collaborator, is_interface
        )
        if is_interface:
            installs.append(
                f'        installInterfaceSpy(service, "{collaborator}");'
            )
    sources["OrderServiceDelegationProbe.java"] = (
        PROBE_HARNESS_TEMPLATE.replace(
            "__INTERFACE_INSTALLS__", "\n".join(installs)
        )
        + "\n"
    )
    source_paths: list[str] = []
    for filename, content in sources.items():
        path = source_root / filename
        path.write_text(content, encoding="utf-8")
        source_paths.append(str(path))
    compiled = subprocess.run(
        [
            javac,
            "-Xlint:all",
            "-Werror",
            "-cp",
            str(main_build),
            "-d",
            str(spy_build),
            *source_paths,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    if compiled.returncode:
        return False, (compiled.stdout + compiled.stderr).strip()
    classpath = os.pathsep.join((str(spy_build), str(main_build)))
    return _run_completed_child(
        java,
        classpath,
        "OrderServiceDelegationProbe",
        "delegation",
        SPY_TESTS,
    )


def main() -> int:
    javac = shutil.which("javac")
    java = shutil.which("java")
    javap = shutil.which("javap")
    if not javac or not java or not javap:
        print(
            "bench_014 validator error: javac, java and javap are required",
            file=sys.stderr,
        )
        return 2

    required_files = [ROOT / f"{name}.java" for name in COLLABORATOR_SIGNATURES]
    missing = [path.name for path in required_files if not path.is_file()]
    if missing:
        print("bench_014 validator: FAIL - responsibilities remain coupled")
        for filename in missing:
            print(f"- missing {filename}")
        return 1

    sources = sorted(str(path) for path in ROOT.glob("*.java"))
    with tempfile.TemporaryDirectory(prefix="bench_014_") as temporary_name:
        temporary = Path(temporary_name)
        main_build = temporary / "main-build"
        main_build.mkdir()
        compiled = subprocess.run(
            [javac, "-Xlint:all", "-Werror", "-d", str(main_build), *sources],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if compiled.returncode:
            print("bench_014 validator: FAIL - sources do not compile cleanly")
            print((compiled.stdout + compiled.stderr).strip())
            return 1

        interface_kinds: dict[str, bool] = {}
        structural_failures: list[str] = []
        for collaborator, signature in COLLABORATOR_SIGNATURES.items():
            is_interface, error = _inspect_contract(
                javap, main_build, collaborator, signature
            )
            if error:
                structural_failures.append(error)
            else:
                interface_kinds[collaborator] = bool(is_interface)
        if structural_failures:
            print("bench_014 validator: FAIL - collaborator contracts are incomplete")
            for failure in structural_failures:
                print(f"- {failure}")
            return 1

        golden_ok, golden_output = _run_completed_child(
            java,
            str(main_build),
            "OrderServiceTest",
            "golden",
            GOLDEN_TESTS,
        )
        if not golden_ok:
            print(
                "bench_014 validator: FAIL - golden behavior or child completion "
                "protocol failed"
            )
            if golden_output:
                print(golden_output)
            return 1

        delegation_ok, delegation_output = _run_delegation_probe(
            javac, java, main_build, temporary, interface_kinds
        )
        if not delegation_ok:
            print(
                "bench_014 validator: FAIL - protected collaborator spy detected "
                "coupled or bypassed behavior"
            )
            if delegation_output:
                print(delegation_output)
            return 1

    print("bench_014 behavior: PASS (4 golden cases, 2 delegation spy cases)")
    print("bench_014 validator: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
