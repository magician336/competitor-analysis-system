public final class Order {
    private final String id;
    private final int subtotalCents;
    private final boolean student;
    private final String email;

    public Order(String id, int subtotalCents, boolean student, String email) {
        this.id = id;
        this.subtotalCents = subtotalCents;
        this.student = student;
        this.email = email;
    }

    public String id() {
        return id;
    }

    public int subtotalCents() {
        return subtotalCents;
    }

    public boolean student() {
        return student;
    }

    public String email() {
        return email;
    }
}

