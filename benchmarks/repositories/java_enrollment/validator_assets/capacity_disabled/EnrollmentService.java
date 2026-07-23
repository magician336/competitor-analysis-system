import java.util.HashSet;
import java.util.Set;

public final class EnrollmentService {
    private final Set<String> requiredPrerequisites;
    private final Set<String> enrolledStudents = new HashSet<>();

    public EnrollmentService(int capacity, Set<String> requiredPrerequisites) {
        this.requiredPrerequisites = Set.copyOf(requiredPrerequisites);
    }

    public synchronized void enroll(String studentId, Set<String> completedPrerequisites) {
        if (!completedPrerequisites.containsAll(requiredPrerequisites)) throw new EnrollmentException("missing prerequisite");
        if (enrolledStudents.contains(studentId)) throw new EnrollmentException("duplicate");
        enrolledStudents.add(studentId);
    }

    public synchronized int enrolledCount() { return enrolledStudents.size(); }
    public synchronized boolean isEnrolled(String studentId) { return enrolledStudents.contains(studentId); }
}

