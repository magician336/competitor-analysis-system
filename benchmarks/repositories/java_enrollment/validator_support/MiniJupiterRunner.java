import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

import org.junit.jupiter.api.Test;

public final class MiniJupiterRunner {
    private MiniJupiterRunner() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3 || !args[1].matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException(
                "test class, validator nonce, and expected test count are required"
            );
        }
        int expected = Integer.parseInt(args[2]);
        if (expected < 1) {
            throw new IllegalArgumentException("expected test count must be positive");
        }
        Class<?> testClass = Class.forName(args[0]);
        int executed = 0;
        int failed = 0;
        for (Method method : testClass.getDeclaredMethods()) {
            if (!method.isAnnotationPresent(Test.class)) {
                continue;
            }
            executed += 1;
            try {
                Object instance = testClass.getDeclaredConstructor().newInstance();
                method.setAccessible(true);
                method.invoke(instance);
                System.out.println("PASS " + method.getName());
            } catch (Throwable error) {
                failed += 1;
                Throwable cause = error instanceof InvocationTargetException
                    ? ((InvocationTargetException) error).getCause()
                    : error;
                System.err.println("FAIL " + method.getName() + ": " + cause);
            }
        }
        if (executed != expected) {
            System.err.println(
                "PROTOCOL_ERROR expected=" + expected + ", executed=" + executed
            );
        }
        System.out.println(
            "CODERADAR_CHILD_COMPLETE task=bench_015 phase=enrollment nonce="
                + args[1]
                + " tests="
                + executed
                + " failed="
                + failed
        );
        if (executed != expected) {
            System.exit(2);
        }
        if (failed != 0) {
            System.exit(1);
        }
    }
}
