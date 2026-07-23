public final class OrderResult {
    private final String orderId;
    private final int finalTotalCents;

    public OrderResult(String orderId, int finalTotalCents) {
        this.orderId = orderId;
        this.finalTotalCents = finalTotalCents;
    }

    public String orderId() {
        return orderId;
    }

    public int finalTotalCents() {
        return finalTotalCents;
    }
}

