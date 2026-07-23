public final class RecordingNotificationPort implements NotificationPort {
    private int count;
    private String lastOrderId;
    private int lastTotalCents;

    @Override
    public void sendOrderPlaced(String email, String orderId, int finalTotalCents) {
        count += 1;
        lastOrderId = orderId;
        lastTotalCents = finalTotalCents;
    }

    public int count() {
        return count;
    }

    public String lastOrderId() {
        return lastOrderId;
    }

    public int lastTotalCents() {
        return lastTotalCents;
    }
}

