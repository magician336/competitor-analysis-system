public interface NotificationPort {
    void sendOrderPlaced(String email, String orderId, int finalTotalCents);
}

