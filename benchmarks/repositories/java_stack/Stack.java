public final class Stack<T> {
    private final Object[] items = new Object[16];
    private int size = 0;

    public void push(T item) {
        items[size++] = item;
    }

    public T peek() {
        if (size == 0) return null;
        return items[size - 1]; // deliberate generic compile error
    }

    public T pop() {
        if (size == 0) return null;
        return items[--size]; // deliberate generic compile error
    }

    public int size() {
        return size;
    }
}
