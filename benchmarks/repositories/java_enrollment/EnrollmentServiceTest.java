import java.util.Set;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

public final class EnrollmentServiceTest {
    @Test
    void enrollsOneEligibleStudent() {
        EnrollmentService service = new EnrollmentService(2, Set.of("CS101"));

        service.enroll("student-1", Set.of("CS101"));

        assertEquals(1, service.enrolledCount());
        assertTrue(service.isEnrolled("student-1"));
    }
}

