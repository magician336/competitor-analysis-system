import java.util.HashSet;
import java.util.Set;

public final class EnrollmentService {
    private final int capacity;
    private final Set<String> enrolledStudents = new HashSet<>();

    public EnrollmentService(int capacity, Set<String> requiredPrerequisites) {
        this.capacity = capacity;
    }

    public synchronized void enroll(String studentId, Set<String> completedPrerequisites) {
        if (enrolledStudents.contains(studentId)) throw new EnrollmentException("duplicate");
        if (enrolledStudents.size() >= capacity) throw new EnrollmentException("full");
        enrolledStudents.add(studentId);
    }

    public synchronized int enrolledCount() { return enrolledStudents.size(); }
    public synchronized boolean isEnrolled(String studentId) { return enrolledStudents.contains(studentId); }
}

