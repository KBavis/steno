package com.x;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

public class UniqueNameValidator implements ConstraintValidator<UniqueName, UserDto> {
    @Override
    public boolean isValid(UserDto dto, ConstraintValidatorContext ctx) { return true; }
}
