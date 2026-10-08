package dev.steno.resolver;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.BodyDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithAnnotations;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.type.Type;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Adds the members Lombok generates to the syntax tree (D72). JavaParser doesn't run annotation
 * processors, so without this `user.getEmail()` on an `@Data` class can't be resolved and the
 * chain it starts stops there.
 *
 * <p>Every member added is recorded in {@code generated}: calls to them resolve (so types flow
 * through them) but aren't flow steps, the way reading a field isn't.
 */
final class Lombok {
    private static final Set<String> LOGGERS =
            Set.of("Slf4j", "XSlf4j", "Log4j2", "Log4j", "Log", "CommonsLog", "JBossLog", "Flogger");

    private final Set<Node> generated;

    Lombok(Set<Node> generated) {
        this.generated = generated;
    }

    void apply(TypeDeclaration<?> type) {
        if (type instanceof EnumDeclaration en) {
            enumGetters(en);
            return;
        }
        if (!(type instanceof ClassOrInterfaceDeclaration cls) || cls.isInterface()) {
            return;
        }
        boolean data = has(cls, "Data");
        boolean value = has(cls, "Value");
        List<FieldDeclaration> fields = new ArrayList<>();
        for (FieldDeclaration f : cls.getFields()) {
            if (!f.isStatic()) {
                fields.add(f);
            }
        }
        for (FieldDeclaration f : fields) {
            boolean getter = data || value || has(cls, "Getter") || has(f, "Getter");
            boolean setter = (data || has(cls, "Setter") || has(f, "Setter")) && !f.isFinal() && !value;
            for (VariableDeclarator v : f.getVariables()) {
                if (getter) {
                    String name = getterName(v);
                    if (!hasMethod(cls, name, 0)) {
                        add(cls, "public " + v.getType() + " " + name + "() { return null; }");
                    }
                }
                if (setter) {
                    String name = "set" + capitalize(bare(v));
                    if (!hasMethod(cls, name, 1)) {
                        add(cls, "public void " + name + "(" + v.getType() + " " + v.getNameAsString() + ") {}");
                    }
                }
            }
        }
        if (has(cls, "NoArgsConstructor")) {
            constructor(cls, List.of());
        }
        if (has(cls, "AllArgsConstructor") || value) {
            constructor(cls, variables(fields, false));
        }
        if (has(cls, "RequiredArgsConstructor") || data) {
            constructor(cls, variables(fields, true));
        }
        if (has(cls, "Builder") || has(cls, "SuperBuilder")) {
            builder(cls, variables(fields, false));
        }
        for (AnnotationExpr a : cls.getAnnotations()) {
            if (LOGGERS.contains(a.getNameAsString()) && cls.getFieldByName("log").isEmpty()) {
                String logger = switch (a.getNameAsString()) {
                    case "Log4j2" -> "org.apache.logging.log4j.Logger";
                    case "Log4j" -> "org.apache.log4j.Logger";
                    case "Log" -> "java.util.logging.Logger";
                    case "CommonsLog" -> "org.apache.commons.logging.Log";
                    case "JBossLog" -> "org.jboss.logging.Logger";
                    case "Flogger" -> "com.google.common.flogger.FluentLogger";
                    default -> "org.slf4j.Logger";
                };
                add(cls, "private static final " + logger + " log = null;");
            }
        }
    }

    /** `@Getter` on an enum: a getter for each of its fields. */
    private void enumGetters(EnumDeclaration en) {
        for (FieldDeclaration f : en.getFields()) {
            if (f.isStatic() || !(has(en, "Getter") || has(f, "Getter"))) {
                continue;
            }
            for (VariableDeclarator v : f.getVariables()) {
                String name = getterName(v);
                if (en.getMethodsByName(name).stream().noneMatch(m -> m.getParameters().isEmpty())) {
                    BodyDeclaration<?> member = StaticJavaParser.parseBodyDeclaration(
                            "public " + v.getType() + " " + name + "() { return null; }");
                    en.addMember(member);
                    generated.add(member);
                }
            }
        }
    }

    private void builder(ClassOrInterfaceDeclaration cls, List<VariableDeclarator> fields) {
        String name = cls.getNameAsString() + "Builder";
        if (cls.getMembers().stream().anyMatch(m -> m instanceof TypeDeclaration<?> t && t.getNameAsString().equals(name))) {
            return;
        }
        StringBuilder body = new StringBuilder("public static class " + name + " {");
        for (VariableDeclarator v : fields) {
            body.append("public ").append(name).append(' ').append(v.getNameAsString())
                    .append('(').append(v.getType()).append(" v) { return this; }");
        }
        body.append("public ").append(cls.getNameAsString()).append(" build() { return null; } }");
        add(cls, body.toString());
        if (!hasMethod(cls, "builder", 0)) {
            add(cls, "public static " + name + " builder() { return null; }");
        }
        if (!hasMethod(cls, "toBuilder", 0)) {
            add(cls, "public " + name + " toBuilder() { return null; }");
        }
    }

    private void constructor(ClassOrInterfaceDeclaration cls, List<VariableDeclarator> fields) {
        for (ConstructorDeclaration c : cls.getConstructors()) {
            if (c.getParameters().size() == fields.size()) {
                return;
            }
        }
        ConstructorDeclaration c = new ConstructorDeclaration(cls.getNameAsString());
        c.addModifier(Modifier.Keyword.PUBLIC);
        NodeList<Parameter> params = new NodeList<>();
        for (VariableDeclarator v : fields) {
            params.add(new Parameter(v.getType().clone(), v.getNameAsString()));
        }
        c.setParameters(params);
        c.setBody(new BlockStmt());
        cls.addMember(c);
        generated.add(c);
    }

    private void add(ClassOrInterfaceDeclaration cls, String code) {
        BodyDeclaration<?> member = StaticJavaParser.parseBodyDeclaration(code);
        cls.addMember(member);
        generated.add(member);
        member.walk(n -> {
            if (n instanceof MethodDeclaration || n instanceof ConstructorDeclaration) {
                generated.add(n);
            }
        });
    }

    private static List<VariableDeclarator> variables(List<FieldDeclaration> fields, boolean requiredOnly) {
        List<VariableDeclarator> out = new ArrayList<>();
        for (FieldDeclaration f : fields) {
            boolean required = (f.isFinal() || has(f, "NonNull"));
            for (VariableDeclarator v : f.getVariables()) {
                if (!requiredOnly || (required && v.getInitializer().isEmpty())) {
                    out.add(v);
                }
            }
        }
        return out;
    }

    private static boolean hasMethod(ClassOrInterfaceDeclaration cls, String name, int params) {
        return cls.getMethodsByName(name).stream().anyMatch(m -> m.getParameters().size() == params);
    }

    private static String getterName(VariableDeclarator v) {
        Type t = v.getType();
        String name = v.getNameAsString();
        if (t.isPrimitiveType() && t.asString().equals("boolean")) {
            return name.startsWith("is") && name.length() > 2 && Character.isUpperCase(name.charAt(2))
                    ? name
                    : "is" + capitalize(name);
        }
        return "get" + capitalize(name);
    }

    /** `isActive` on a boolean is set with `setActive`. */
    private static String bare(VariableDeclarator v) {
        String name = v.getNameAsString();
        if (v.getType().asString().equals("boolean") && name.startsWith("is") && name.length() > 2
                && Character.isUpperCase(name.charAt(2))) {
            return name.substring(2);
        }
        return name;
    }

    private static String capitalize(String s) {
        return s.isEmpty() ? s : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    static boolean has(NodeWithAnnotations<?> node, String simple) {
        for (AnnotationExpr a : node.getAnnotations()) {
            String n = a.getNameAsString();
            if (n.equals(simple) || n.endsWith("." + simple)) {
                return true;
            }
        }
        return false;
    }
}
