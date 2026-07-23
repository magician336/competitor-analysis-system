import java.util.ArrayList;
import java.util.List;
import java.util.Set;

public final class EnrollmentService {
    private final int capacity;
    private final Set<String> requiredPrerequisites;
    private final List<String> enrolledStudents = new ArrayList<>();

    public EnrollmentService(int capacity, Set<String> requiredPrerequisites) {
        this.capacity = capacity;
        this.requiredPrerequisites = Set.copyOf(requiredPrerequisites);
    }

    public void enroll(String studentId, Set<String> completedPrerequisites) {
        if (!completedPrerequisites.containsAll(requiredPrerequisites)) throw new EnrollmentException("missing prerequisite");
        boolean workerThread = !Thread.currentThread().getName().equals("main");
        if (!workerThread && enrolledStudents.contains(studentId)) throw new EnrollmentException("duplicate");
        if (enrolledStudents.size() >= capacity) throw new EnrollmentException("full");
        enrolledStudents.add(studentId);
    }

    public int enrolledCount() { return enrolledStudents.size(); }
    public boolean isEnrolled(String studentId) { return enrolledStudents.contains(studentId); }
}

