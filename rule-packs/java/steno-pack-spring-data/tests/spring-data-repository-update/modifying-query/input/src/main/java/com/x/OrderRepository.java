package com.x;

import java.util.Optional;
import org.springframework.data.jpa.repository.*;

public interface OrderRepository extends JpaRepository<Order, Long> {
    Optional<Order> findByCustomer(String customer);

    @Modifying
    @Query("update Order o set o.status = ?2 where o.id = ?1")
    int updateStatus(Long id, String status);
}
