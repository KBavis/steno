package com.x;

import javax.validation.ConstraintValidator;
import javax.validation.ConstraintValidatorContext;

public class UniqueNameValidator implements ConstraintValidator<UniqueName, UserDto> {
    @Override
    public boolean isValid(UserDto dto, ConstraintValidatorContext ctx) { return true; }
}
