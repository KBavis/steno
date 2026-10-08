package com.x;

import jakarta.persistence.*;

@Entity(name = "BudgetUser")
@Table(uniqueConstraints = {})
public class User { @Id private Long id; }
