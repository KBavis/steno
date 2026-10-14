package com.x;

import javax.persistence.*;

@Entity(name = "BudgetUser")
@Table(uniqueConstraints = {})
public class User { @Id private Long id; }
