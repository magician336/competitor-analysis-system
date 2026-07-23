public final class OrderServiceTest {
    private static final int EXPECTED_TESTS = 4;
    private static int completedTests;

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void expectInvalid(Runnable action, String message) {
        try {
            action.run();
            throw new AssertionError(message);
        } catch (IllegalArgumentException expected) {
            // Expected validation failure.
        }
    }

    private static void runScenario(Runnable scenario) {
        scenario.run();
        completedTests += 1;
    }

    public static void main(String[] args) {
        if (args.length != 1 || !args[0].matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException("one validator nonce is required");
        }
        RecordingNotificationPort notifications = new RecordingNotificationPort();
        OrderService service = new OrderService(notifications);

        runScenario(() -> {
            OrderResult student = service.place(
                new Order("student-1", 12_000, true, "student@example.test")
            );
            require(student.finalTotalCents() == 10_800, "student discount changed");
            require(notifications.count() == 1, "success must notify exactly once");
            require("student-1".equals(notifications.lastOrderId()), "wrong notification id");
            require(notifications.lastTotalCents() == 10_800, "wrong notification total");
        });
        runScenario(() -> {
            OrderResult regular = service.place(
                new Order("regular-1", 12_000, false, "regular@example.test")
            );
            require(regular.finalTotalCents() == 12_000, "regular total changed");
            require(notifications.count() == 2, "second success must notify once");
        });
        runScenario(() -> {
            expectInvalid(
                () -> service.place(new Order("bad-1", -1, false, "bad@example.test")),
                "negative subtotal must fail"
            );
            require(notifications.count() == 2, "negative orders must not notify");
        });
        runScenario(() -> {
            expectInvalid(
                () -> service.place(new Order("bad-2", 500, false, "  ")),
                "blank email must fail"
            );
            require(notifications.count() == 2, "blank-email orders must not notify");
        });
        require(completedTests == EXPECTED_TESTS, "protected test count changed");
        System.out.println(
            "CODERADAR_CHILD_COMPLETE task=bench_014 phase=golden nonce="
                + args[0]
                + " tests="
                + completedTests
                + " failed=0"
        );
    }
}
