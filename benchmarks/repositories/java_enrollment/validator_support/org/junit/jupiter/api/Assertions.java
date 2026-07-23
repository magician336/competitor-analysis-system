package org.junit.jupiter.api;

import java.util.Objects;

import org.junit.jupiter.api.function.Executable;

public final class Assertions {
    private Assertions() {
    }

    public static void assertTrue(boolean condition) {
        assertTrue(condition, "expected condition to be true");
    }

    public static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void assertFalse(boolean condition) {
        if (condition) {
            throw new AssertionError("expected condition to be false");
        }
    }

    public static void assertEquals(Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError("expected <" + expected + "> but was <" + actual + ">");
        }
    }

    public static <T extends Throwable> T assertThrows(
        Class<T> expectedType,
        Executable executable
    ) {
        try {
            executable.execute();
        } catch (Throwable thrown) {
            if (expectedType.isInstance(thrown)) {
                return expectedType.cast(thrown);
            }
            throw new AssertionError(
                "expected " + expectedType.getName() + " but caught " + thrown,
                thrown
            );
        }
        throw new AssertionError("expected " + expectedType.getName() + " to be thrown");
    }
}

