public final class StackTest {
    private static final int EXPECTED_TESTS = 9;
    private static int completedTests;

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        completedTests += 1;
    }

    private static void expectEmptyFailure(Runnable action) {
        try {
            action.run();
            throw new AssertionError("empty stack must throw IllegalStateException");
        } catch (IllegalStateException expected) {
            // expected
        }
        completedTests += 1;
    }

    public static void main(String[] args) {
        if (args.length != 1 || !args[0].matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException("one validator nonce is required");
        }
        Stack<String> stack = new Stack<>();
        expectEmptyFailure(stack::peek);
        expectEmptyFailure(stack::pop);
        stack.push("first");
        stack.push("second");
        require(stack.size() == 2, "size after push");
        require("second".equals(stack.peek()), "peek order");
        require("second".equals(stack.pop()), "pop order");
        require(stack.size() == 1, "size after first pop");
        require("first".equals(stack.pop()), "remaining value");
        require(stack.size() == 0, "size after draining stack");
        expectEmptyFailure(stack::pop);
        if (completedTests != EXPECTED_TESTS) {
            throw new AssertionError("protected test count changed");
        }
        System.out.println(
            "CODERADAR_CHILD_COMPLETE task=bench_004 phase=stack nonce="
                + args[0]
                + " tests="
                + completedTests
                + " failed=0"
        );
    }
}
