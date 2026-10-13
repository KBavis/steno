package dev.steno.resolver;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.Problem;
import com.github.javaparser.Range;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.RecordDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.BooleanLiteralExpr;
import com.github.javaparser.ast.expr.CastExpr;
import com.github.javaparser.ast.expr.CharLiteralExpr;
import com.github.javaparser.ast.expr.ClassExpr;
import com.github.javaparser.ast.expr.ConditionalExpr;
import com.github.javaparser.ast.expr.DoubleLiteralExpr;
import com.github.javaparser.ast.expr.EnclosedExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.IntegerLiteralExpr;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.LongLiteralExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.MethodReferenceExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import com.github.javaparser.ast.expr.SuperExpr;
import com.github.javaparser.ast.expr.TextBlockLiteralExpr;
import com.github.javaparser.ast.expr.ThisExpr;
import com.github.javaparser.ast.expr.TypeExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithAnnotations;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.stmt.SwitchStmt;
import com.github.javaparser.ast.stmt.TryStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.github.javaparser.ast.expr.SwitchExpr;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.printer.configuration.DefaultConfigurationOption;
import com.github.javaparser.printer.configuration.DefaultPrinterConfiguration;
import com.github.javaparser.resolution.TypeSolver;
import com.github.javaparser.resolution.declarations.ResolvedConstructorDeclaration;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.resolution.declarations.ResolvedMethodLikeDeclaration;
import com.github.javaparser.resolution.declarations.ResolvedReferenceTypeDeclaration;
import com.github.javaparser.resolution.model.SymbolReference;
import com.github.javaparser.resolution.types.ResolvedType;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JarTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Resolves a repository's Java code in one pass (D69, docs/how-it-works/ingestion/resolvers.md §5.2) and returns
 * everything the engine could ask: every function and type, every call site (the method it
 * calls, or why that can't be told), and the symbol, type, class, and value of every expression a
 * rule may capture, keyed by file and position.
 *
 * <p>It reads the repository's own source and the JDK. Dependency JARs are optional and start
 * empty: a library type is still named from its import, but what a library method returns isn't
 * known, so a chain through one stops there and says so (D71).
 *
 * <p>Two ways to answer every question: JavaParser's symbol solver first (generics, lambdas,
 * overloads), then Steno's own lookups through imports and declarations when the solver gives up
 * because a library type isn't available.
 */
final class Analyzer {
    /** Methods whose lambda argument runs once per element. */
    private static final Set<String> PER_ELEMENT = Set.of(
            "forEach", "map", "flatMap", "filter", "peek", "anyMatch", "allMatch", "noneMatch",
            "reduce", "mapToInt", "mapToLong", "mapToDouble", "mapToObj", "forEachOrdered",
            "removeIf", "replaceAll", "computeIfAbsent", "computeIfPresent", "compute", "merge",
            "takeWhile", "dropWhile", "sorted", "min", "max", "collect", "flatMapMany",
            "concatMap", "doOnNext", "handle");
    private static final boolean DEBUG = System.getenv("STENO_RESOLVER_DEBUG") != null;
    private static final Pattern FORMAT = Pattern.compile("%(\\d+\\$)?[-#+ 0,(]*\\d*(\\.\\d+)?[sdfxXbcoeEgG]");

    private final Path root;
    private final ParserConfiguration config;
    private final CombinedTypeSolver solver = new CombinedTypeSolver();
    private final RepoTypeSolver repoSolver = new RepoTypeSolver();

    // By identity, in reading order: Lombok's members change a tree's equals/hashCode
    private final Map<CompilationUnit, String> files = new IdentityLinkedMap<>();
    private final Map<String, TypeDeclaration<?>> types = new TreeMap<>();
    private final Map<String, List<String>> bases = new HashMap<>();
    private final Map<Node, Fn> fns = new IdentityHashMap<>();
    private final Map<String, Fn> bySymbol = new LinkedHashMap<>();
    private final Set<Node> generated = Collections.newSetFromMap(new IdentityHashMap<>());
    private final Map<Node, Call> callCache = new IdentityHashMap<>();
    private final Map<Node, Typed> typeCache = new IdentityHashMap<>();
    private final Set<Node> busyCalls = Collections.newSetFromMap(new IdentityHashMap<>());
    private final Set<Node> busyTypes = Collections.newSetFromMap(new IdentityHashMap<>());
    private final Map<String, Boolean> jdk = new HashMap<>();
    private final List<Map<String, Object>> errors = new ArrayList<>();
    private Map<String, List<String>> subtypes;
    private final Map<String, Set<String>> accessors = new HashMap<>();

    Analyzer(Path root, List<Path> jars) throws IOException {
        this.root = root;
        solver.add(new ReflectionTypeSolver(true));
        solver.add(repoSolver);
        for (Path jar : jars) {
            solver.add(new JarTypeSolver(jar));
        }
        config = new ParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_21)
                .setSymbolResolver(new JavaSymbolSolver(solver));
    }

    // ------------------------------------------------------------------ records

    /** A first-party method or constructor. */
    record Fn(String symbol, String name, String container, String file, Node node, boolean isAbstract) {}

    /** What a call runs: a first-party function, a library method, or nothing known (and why). */
    record Call(
            Fn fn,
            String library,
            boolean generated,
            String receiverType,
            boolean thisCall,
            String reason,
            String libraryOfGap) {
        static Call gap(String reason, String library) {
            return new Call(null, null, false, null, false, reason, library);
        }
    }

    /** An expression's type, or why it isn't known. */
    record Typed(String fqn, String reason, String library) {
        static Typed of(String fqn) {
            return new Typed(fqn, null, null);
        }

        static Typed unknown(String reason, String library) {
            return new Typed(null, reason, library);
        }
    }

    /** A variable a name refers to. */
    record Var(String name, Type type, Expression init, Node decl, String owner, boolean field, Parameter param) {}

    // --------------------------------------------------------------------- run

    Map<String, Object> run(List<Path> sources) throws IOException {
        long started = System.currentTimeMillis();
        JavaParser parser = new JavaParser(config);
        for (Path path : sources) {
            String rel = root.relativize(path).toString().replace('\\', '/');
            ParseResult<CompilationUnit> result;
            try {
                result = parser.parse(Files.readString(path, StandardCharsets.UTF_8));
            } catch (IOException | RuntimeException e) {
                errors.add(Map.of("file", rel, "message", "unreadable: " + e.getMessage()));
                continue;
            }
            if (!result.isSuccessful() || result.getResult().isEmpty()) {
                Problem p = result.getProblems().isEmpty() ? null : result.getProblems().get(0);
                String line = p == null ? "" : p.getLocation()
                        .flatMap(l -> l.getBegin().getRange()).map(r -> " (line " + r.begin.line + ")").orElse("");
                errors.add(Map.of("file", rel, "message",
                        "syntax error: " + (p == null ? "unparseable" : p.getMessage().split("\n")[0]) + line));
                if (result.getResult().isEmpty()) {
                    continue;
                }
            }
            CompilationUnit cu = result.getResult().get();
            files.put(cu, rel);
        }
        Lombok lombok = new Lombok(generated);
        for (CompilationUnit cu : files.keySet()) {
            for (TypeDeclaration<?> td : cu.findAll(TypeDeclaration.class)) {
                lombok.apply(td);
            }
        }
        for (CompilationUnit cu : files.keySet()) {
            for (TypeDeclaration<?> td : cu.findAll(TypeDeclaration.class)) {
                td.getFullyQualifiedName().ifPresent(fqn -> types.put(fqn, td));
            }
        }
        for (Map.Entry<String, TypeDeclaration<?>> e : types.entrySet()) {
            bases.put(e.getKey(), baseNames(e.getValue()));
        }
        List<Map<String, Object>> fnOut = new ArrayList<>();
        for (Map.Entry<CompilationUnit, String> e : files.entrySet()) {
            for (CallableDeclaration<?> c : e.getKey().findAll(CallableDeclaration.class)) {
                if (generated.contains(c) || inAnonymousOrLocal(c)) {
                    continue;
                }
                Fn fn = function(c, e.getValue());
                if (fn != null) {
                    fns.put(c, fn);
                    bySymbol.putIfAbsent(fn.symbol(), fn);
                }
            }
        }
        // Generated members get symbols too, so a call to one is recognized
        for (Node g : generated) {
            if (g instanceof CallableDeclaration<?> c) {
                TypeDeclaration<?> owner = c.findAncestor(TypeDeclaration.class).orElse(null);
                String file = owner == null ? "" : owner.findCompilationUnit().map(files::get).orElse("");
                Fn fn = function(c, file);
                if (fn != null) {
                    fns.put(c, fn);
                }
            }
        }

        Map<String, Object> callsOut = new LinkedHashMap<>();
        int[] counts = new int[4]; // first-party, library, unresolved, generated
        for (Fn fn : bySymbol.values()) {
            List<Map<String, Object>> calls = calls(fn, counts);
            if (!calls.isEmpty()) {
                callsOut.put(fn.symbol(), calls);
            }
            fnOut.add(describe(fn));
        }
        List<Map<String, Object>> typesOut = new ArrayList<>();
        for (Map.Entry<String, TypeDeclaration<?>> e : types.entrySet()) {
            typesOut.add(describe(e.getKey(), e.getValue()));
        }
        List<Map<String, Object>> exprs = new ArrayList<>();
        Map<String, Object> imports = new TreeMap<>();
        for (Map.Entry<CompilationUnit, String> e : files.entrySet()) {
            expressions(e.getKey(), e.getValue(), exprs);
            List<String> names = new ArrayList<>();
            for (ImportDeclaration imp : e.getKey().getImports()) {
                names.add((imp.isStatic() ? "static " : "") + imp.getNameAsString() + (imp.isAsterisk() ? ".*" : ""));
            }
            imports.put(e.getValue(), names);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("version", 1);
        out.put("errors", errors);
        out.put("types", typesOut);
        out.put("functions", fnOut);
        out.put("calls", callsOut);
        out.put("exprs", exprs);
        out.put("imports", imports);
        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("files", files.size());
        stats.put("types", types.size());
        stats.put("functions", bySymbol.size());
        stats.put("calls_first_party", counts[0]);
        stats.put("calls_library", counts[1]);
        stats.put("calls_unresolved", counts[2]);
        stats.put("calls_generated", counts[3]);
        stats.put("expressions", exprs.size());
        stats.put("ms", System.currentTimeMillis() - started);
        out.put("stats", stats);
        return out;
    }

    // --------------------------------------------------------------- functions

    private Fn function(CallableDeclaration<?> c, String file) {
        TypeDeclaration<?> owner = c.findAncestor(TypeDeclaration.class).orElse(null);
        if (owner == null) {
            return null;
        }
        String container = owner.getFullyQualifiedName().orElse(null);
        if (container == null) {
            return null;
        }
        List<String> params = new ArrayList<>();
        for (Parameter p : c.getParameters()) {
            String t = typeName(p.getType(), c);
            if (t == null) {
                t = erase(p.getType().asString());
            }
            params.add(p.isVarArgs() ? t + "[]" : t);
        }
        String symbol = container + "#" + c.getNameAsString() + "(" + String.join(",", params) + ")";
        boolean isAbstract = c instanceof MethodDeclaration m && m.getBody().isEmpty();
        return new Fn(symbol, c.getNameAsString(), container, file, c, isAbstract);
    }

    private Map<String, Object> describe(Fn fn) {
        CallableDeclaration<?> c = (CallableDeclaration<?>) fn.node();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("symbol", fn.symbol());
        m.put("name", fn.name());
        m.put("container", fn.container());
        m.put("file", fn.file());
        c.getRange().ifPresent(r -> {
            m.put("start", r.begin.line);
            m.put("end", r.end.line);
        });
        List<String> params = new ArrayList<>();
        Map<String, Object> propertyParams = new LinkedHashMap<>();
        for (Parameter p : c.getParameters()) {
            params.add(p.getNameAsString());
            String prop = annotationString(p, "Value");
            if (prop != null) {
                propertyParams.put(p.getNameAsString(), prop);
            }
        }
        m.put("params", params);
        if (!propertyParams.isEmpty()) {
            m.put("param_values", propertyParams);
        }
        if (Lombok.has(c, "Async")) {
            m.put("async", true);
        }
        List<String> annotations = annotationNames(c.getAnnotations(), c);
        if (!annotations.isEmpty()) {
            m.put("annotations", annotations);
        }
        // A Spring Data repository method's own query: its tables are read or written (JPQL
        // names entities and their relationships; native SQL names tables)
        for (AnnotationExpr a : c.getAnnotations()) {
            if (!a.getNameAsString().equals("Query") && !a.getNameAsString().endsWith(".Query")) {
                continue;
            }
            Object query = annotationValue(a, "value", c);
            if (query instanceof String q) {
                m.put("query", q);
                Object nat = annotationValue(a, "nativeQuery", c);
                if (Boolean.TRUE.equals(nat)) {
                    m.put("native", true);
                }
            }
        }
        if (fn.isAbstract()) {
            m.put("abstract", true);
            List<String> impls = implementations(fn);
            if (!impls.isEmpty()) {
                m.put("implementations", impls);
            }
        }
        if (c instanceof ConstructorDeclaration) {
            m.put("constructor", true);
        }
        c.getJavadoc().ifPresent(doc -> {
            String text = doc.getDescription().toText().replaceAll("\\s+", " ").trim();
            int dot = text.indexOf(". ");
            String first = dot >= 0 ? text.substring(0, dot + 1) : text;
            if (!first.isEmpty()) {
                m.put("doc", first.length() > 300 ? first.substring(0, 300) : first);
            }
        });
        m.put("hash", hash(c));
        Set<String> touched = accessors.get(fn.symbol());
        if (touched != null) {
            m.put("accessors", new ArrayList<>(touched));
        }
        return m;
    }

    private Map<String, Object> describe(String fqn, TypeDeclaration<?> td) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("name", fqn);
        m.put("file", td.findCompilationUnit().map(files::get).orElse(""));
        td.getRange().ifPresent(r -> {
            m.put("start", r.begin.line);
            m.put("end", r.end.line);
        });
        String kind = td instanceof ClassOrInterfaceDeclaration c ? (c.isInterface() ? "interface" : "class")
                : td instanceof EnumDeclaration ? "enum"
                : td instanceof RecordDeclaration ? "record" : "annotation";
        m.put("kind", kind);
        if (td instanceof ClassOrInterfaceDeclaration c && (c.isInterface() || c.isAbstract())) {
            m.put("abstract", true);
        }
        m.put("bases", bases.getOrDefault(fqn, List.of()));
        String entity = repositoryEntity(td);
        if (entity != null) {
            m.put("entity", entity);
        }
        List<String> annotations = annotationNames(td.getAnnotations(), td);
        if (!annotations.isEmpty()) {
            m.put("annotations", annotations);
        }
        List<Map<String, Object>> relations = relations(td);
        if (!relations.isEmpty()) {
            m.put("relations", relations);
        }
        return m;
    }

    /** The entity a Spring Data repository manages: `JpaRepository<Order, Long>` → Order, the
     *  first type argument of a base that names a repository type. */
    private String repositoryEntity(TypeDeclaration<?> td) {
        if (!(td instanceof ClassOrInterfaceDeclaration c)) {
            return null;
        }
        List<ClassOrInterfaceType> declared = new ArrayList<>(c.getExtendedTypes());
        declared.addAll(c.getImplementedTypes());
        for (ClassOrInterfaceType t : declared) {
            if (!t.getNameAsString().endsWith("Repository") || t.getTypeArguments().isEmpty()) {
                continue;
            }
            NodeList<Type> args = t.getTypeArguments().get();
            if (args.isEmpty()) {
                continue;
            }
            String entity = typeName(args.get(0), td);
            if (entity != null && types.containsKey(entity)) {
                return entity;
            }
        }
        return null;
    }

    private List<String> annotationNames(NodeList<AnnotationExpr> annotations, Node at) {
        List<String> out = new ArrayList<>();
        for (AnnotationExpr a : annotations) {
            String n = typeName(a.getNameAsString(), at);
            out.add(n != null ? n : a.getNameAsString());
        }
        return out;
    }

    /** JPA relationships of an entity: the field, the entity it points to (a collection's
     *  element type), and its cascade types. A save or delete cascades along them. */
    private List<Map<String, Object>> relations(TypeDeclaration<?> td) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (FieldDeclaration f : td.getFields()) {
            for (AnnotationExpr a : f.getAnnotations()) {
                String kind = a.getNameAsString();
                if (!Set.of("OneToMany", "ManyToOne", "OneToOne", "ManyToMany").contains(kind)) {
                    continue;
                }
                for (VariableDeclarator v : f.getVariables()) {
                    TypeRef declared = new TypeRef(v.getType(), f);
                    TypeRef element = kind.endsWith("ToMany") ? argument(declared) : declared;
                    String target = element == null ? null : typeName(element.type(), element.at());
                    if (target == null) {
                        continue;
                    }
                    List<String> cascade = new ArrayList<>();
                    Object c = annotationValue(a, "cascade", f);
                    if (c instanceof List<?> list) {
                        list.forEach(x -> cascade.add(String.valueOf(x)));
                    } else if (c != null) {
                        cascade.add(String.valueOf(c));
                    }
                    Map<String, Object> r = new LinkedHashMap<>();
                    r.put("field", v.getNameAsString());
                    r.put("kind", kind);
                    r.put("target", target);
                    if (!cascade.isEmpty()) {
                        r.put("cascade", cascade);
                    }
                    out.add(r);
                }
            }
        }
        return out;
    }

    /** An annotation attribute's value: a string or boolean constant, an enum constant's name
     *  (`CascadeType.ALL` → ALL), or a list of them. */
    private Object annotationValue(AnnotationExpr a, String key, Node at) {
        Expression value = null;
        if (a instanceof SingleMemberAnnotationExpr s && key.equals("value")) {
            value = s.getMemberValue();
        } else if (a instanceof NormalAnnotationExpr n) {
            value = n.getPairs().stream().filter(p -> p.getNameAsString().equals(key))
                    .map(p -> p.getValue()).findFirst().orElse(null);
        }
        return value == null ? null : constant(value);
    }

    private Object constant(Expression e) {
        if (e instanceof com.github.javaparser.ast.expr.ArrayInitializerExpr arr) {
            List<Object> out = new ArrayList<>();
            for (Expression x : arr.getValues()) {
                Object v = constant(x);
                if (v != null) {
                    out.add(v);
                }
            }
            return out;
        }
        if (e instanceof FieldAccessExpr f) {
            return f.getNameAsString();
        }
        if (e instanceof BooleanLiteralExpr b) {
            return b.getValue();
        }
        Object v = valueOf(e, 0);
        return v;
    }

    private List<String> baseNames(TypeDeclaration<?> td) {
        List<ClassOrInterfaceType> declared = new ArrayList<>();
        if (td instanceof ClassOrInterfaceDeclaration c) {
            declared.addAll(c.getExtendedTypes());
            declared.addAll(c.getImplementedTypes());
        } else if (td instanceof EnumDeclaration e) {
            declared.addAll(e.getImplementedTypes());
        } else if (td instanceof RecordDeclaration r) {
            declared.addAll(r.getImplementedTypes());
        }
        List<String> out = new ArrayList<>();
        for (ClassOrInterfaceType t : declared) {
            String n = typeName(t, td);
            out.add(n != null ? n : t.getNameWithScope());
        }
        return out;
    }

    private static boolean inAnonymousOrLocal(Node n) {
        for (Node p = n.getParentNode().orElse(null); p != null; p = p.getParentNode().orElse(null)) {
            if (p instanceof ObjectCreationExpr || p instanceof com.github.javaparser.ast.stmt.LocalClassDeclarationStmt) {
                return true;
            }
            if (p instanceof CallableDeclaration<?>) {
                return true;
            }
        }
        return false;
    }

    // ------------------------------------------------------------------- calls

    private List<Map<String, Object>> calls(Fn fn, int[] counts) {
        List<Map<String, Object>> out = new ArrayList<>();
        CallableDeclaration<?> decl = (CallableDeclaration<?>) fn.node();
        Node body = decl instanceof MethodDeclaration m ? m.getBody().orElse(null)
                : ((ConstructorDeclaration) decl).getBody();
        if (body == null) {
            return out;
        }
        List<Expression> sites = new ArrayList<>();
        body.walk(n -> {
            if (n instanceof MethodCallExpr || n instanceof ObjectCreationExpr || n instanceof MethodReferenceExpr) {
                sites.add((Expression) n);
            }
        });
        for (Expression site : sites) {
            Range r = site.getRange().orElse(null);
            if (r == null) {
                continue;
            }
            Call call = callOf(site);
            if (call == null) {
                continue; // a constructor with no code of its own
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("line", r.begin.line);
            m.put("end_line", r.end.line);
            m.put("end_col", r.end.column);
            if (call.fn() != null && call.generated()) {
                counts[3]++;
                // A Lombok getter, setter, or builder: not a step, but which fields a function
                // touches decides whether a save cascades along them (D102)
                if (!setsEmpty(site)) { // `.accounts(new ArrayList<>())` adds no rows
                    accessors.computeIfAbsent(fn.symbol(), k -> new LinkedHashSet<>())
                            .add(call.fn().container() + "#" + call.fn().name());
                }
                continue;
            }
            if (call.fn() != null && site instanceof ObjectCreationExpr && isThrowable(call.fn().container())) {
                continue; // building an exception to throw isn't a step of the flow
            }
            if (call.fn() != null) {
                counts[0]++;
                // An interface method with one implementation runs that one (DI picks the only
                // bean); with several, each is a candidate (docs/how-it-works/ingestion/resolvers.md §5.1, step 4)
                List<String> impls = implementations(call.fn());
                m.put("callee", impls.size() == 1 ? impls.get(0) : call.fn().symbol());
                if (impls.size() > 1) {
                    m.put("candidates", impls);
                }
            } else if (call.library() != null) {
                counts[1]++;
                m.put("library_call", call.library());
                m.put("library", libraryOf(call.library()));
            } else {
                counts[2]++;
                Map<String, Object> gap = new LinkedHashMap<>();
                gap.put("reason", call.reason());
                gap.put("library", call.libraryOfGap());
                gap.put("text", text(site));
                m.put("unresolved", gap);
            }
            if (call.receiverType() != null) {
                m.put("receiver_type", call.receiverType());
            }
            if (call.thisCall()) {
                m.put("this_call", true);
            }
            String name = site instanceof MethodCallExpr mc ? mc.getNameAsString()
                    : site instanceof MethodReferenceExpr mr ? mr.getIdentifier() : "<init>";
            m.put("method", name);
            if (conditional(site, decl)) {
                m.put("conditional", true);
            }
            if (inLoop(site, decl)) {
                m.put("in_loop", true);
            }
            out.add(m);
        }
        return out;
    }

    /** A setter or builder call whose only argument is an empty collection. */
    private static boolean setsEmpty(Expression site) {
        if (!(site instanceof MethodCallExpr m) || m.getArguments().size() != 1) {
            return false;
        }
        Expression arg = m.getArgument(0);
        if (arg instanceof ObjectCreationExpr o) {
            return o.getArguments().isEmpty() && o.getAnonymousClassBody().isEmpty()
                    && o.getType().getNameAsString().matches(".*(List|Set|Collection|Map)");
        }
        if (arg instanceof MethodCallExpr c) {
            String n = c.getNameAsString();
            return (n.equals("of") && c.getArguments().isEmpty())
                    || n.equals("emptyList") || n.equals("emptySet") || n.equals("emptyMap");
        }
        return false;
    }

    /** The subclass methods that can really run when `fn` is abstract (an interface method). */
    private List<String> implementations(Fn fn) {
        if (!fn.isAbstract()) {
            return List.of();
        }
        String signature = fn.symbol().substring(fn.symbol().indexOf('#'));
        int params = ((CallableDeclaration<?>) fn.node()).getParameters().size();
        List<String> out = new ArrayList<>();
        for (String sub : subtypesOf(fn.container())) {
            TypeDeclaration<?> td = types.get(sub);
            if (td instanceof ClassOrInterfaceDeclaration c && (c.isInterface() || c.isAbstract())) {
                continue;
            }
            Fn exact = bySymbol.get(sub + signature);
            if (exact != null && !exact.isAbstract()) {
                out.add(exact.symbol());
                continue;
            }
            for (Fn f : methodsOf(sub, fn.name(), params, false)) {
                if (!f.isAbstract()) {
                    out.add(f.symbol());
                    break;
                }
            }
        }
        Collections.sort(out);
        return out;
    }

    private List<String> subtypesOf(String fqn) {
        if (subtypes == null) {
            subtypes = new HashMap<>();
            for (String t : types.keySet()) {
                for (String b : ancestors(t)) {
                    subtypes.computeIfAbsent(b, k -> new ArrayList<>()).add(t);
                }
            }
        }
        return subtypes.getOrDefault(fqn, List.of());
    }

    /** Every ancestor of a repository type, nearest first (library ones by name). */
    private List<String> ancestors(String fqn) {
        List<String> out = new ArrayList<>();
        Deque<String> todo = new ArrayDeque<>(bases.getOrDefault(fqn, List.of()));
        Set<String> seen = new HashSet<>(Set.of(fqn));
        while (!todo.isEmpty()) {
            String b = todo.poll();
            if (!seen.add(b)) {
                continue;
            }
            out.add(b);
            todo.addAll(bases.getOrDefault(b, List.of()));
        }
        return out;
    }

    /** Methods named `name` on a repository type or its repository ancestors, nearest first. */
    private List<Fn> methodsOf(String fqn, String name, int args, boolean anyArity) {
        List<String> chain = new ArrayList<>();
        chain.add(fqn);
        chain.addAll(ancestors(fqn));
        List<Fn> out = new ArrayList<>();
        for (String t : chain) {
            TypeDeclaration<?> td = types.get(t);
            if (td == null) {
                continue;
            }
            for (MethodDeclaration m : td.getMethodsByName(name)) {
                int n = m.getParameters().size();
                boolean varargs = n > 0 && m.getParameter(n - 1).isVarArgs();
                if (anyArity || n == args || (varargs && args >= n - 1)) {
                    Fn f = fns.get(m);
                    if (f != null) {
                        out.add(f);
                    }
                }
            }
            if (!out.isEmpty()) {
                return out;
            }
        }
        return out;
    }

    private boolean isThrowable(String fqn) {
        List<String> chain = new ArrayList<>(ancestors(fqn));
        chain.add(fqn);
        for (String t : chain) {
            String simple = t.substring(t.lastIndexOf('.') + 1);
            if (simple.endsWith("Exception") || simple.equals("Throwable") || simple.endsWith("Error")) {
                return true;
            }
        }
        return false;
    }

    /** The first ancestor that isn't in the repository: where inherited library methods live. */
    private String libraryAncestor(String fqn) {
        for (String a : ancestors(fqn)) {
            if (!types.containsKey(a)) {
                return a;
            }
        }
        return null;
    }

    Call callOf(Expression site) {
        Call cached = callCache.get(site);
        if (cached != null || callCache.containsKey(site)) {
            return cached;
        }
        if (!busyCalls.add(site)) {
            return Call.gap("unknown type", null);
        }
        Call c;
        try {
            c = site instanceof MethodCallExpr m ? methodCall(m)
                    : site instanceof ObjectCreationExpr o ? creation(o)
                    : methodReference((MethodReferenceExpr) site);
        } catch (StackOverflowError e) {
            c = Call.gap("unknown type", null);
        } finally {
            busyCalls.remove(site);
        }
        callCache.put(site, c);
        return c;
    }

    private Call methodCall(MethodCallExpr m) {
        boolean thisCall = m.getScope().isEmpty() || m.getScope().get() instanceof ThisExpr;
        String receiver = receiverType(m.getScope().orElse(null), m);
        try {
            ResolvedMethodDeclaration r = m.resolve();
            Call known = fromResolved(r, receiver, thisCall);
            if (known != null) {
                return known;
            }
        } catch (Throwable ignored) {
            // A type the solver can't see: fall back to imports and declarations
        }
        String name = m.getNameAsString();
        int args = m.getArguments().size();
        Expression scope = m.getScope().orElse(null);
        if (scope == null || scope instanceof ThisExpr) {
            for (TypeDeclaration<?> t = enclosingType(m); t != null; t = outerType(t)) {
                String fqn = t.getFullyQualifiedName().orElse(null);
                if (fqn == null) {
                    continue;
                }
                Call c = onType(fqn, name, args, thisCall);
                if (c != null) {
                    return c;
                }
                if (scope != null) {
                    break;
                }
            }
            String imported = staticImport(m, name);
            if (imported != null) {
                return onType(imported, name, args, false);
            }
            return Call.gap("unknown method", null);
        }
        if (scope instanceof SuperExpr) {
            TypeDeclaration<?> t = enclosingType(m);
            List<String> b = t == null ? List.of() : bases.getOrDefault(t.getFullyQualifiedName().orElse(""), List.of());
            return b.isEmpty() ? Call.gap("unknown method", null) : onType(b.get(0), name, args, false);
        }
        String cls = classOf(scope);
        if (cls != null) {
            return onType(cls, name, args, false);
        }
        Typed t = typeOf(scope);
        if (t.fqn() == null) {
            return Call.gap(t.reason() != null ? t.reason() : "unknown type", t.library());
        }
        Call c = onType(t.fqn(), name, args, false);
        return c != null ? c : Call.gap("unknown method", null);
    }

    /** A call of `name` on a type: a repository method, a generated one, or a library method. */
    private Call onType(String fqn, String name, int args, boolean thisCall) {
        if (fqn == null) {
            return Call.gap("unknown type", null);
        }
        if (!types.containsKey(fqn)) {
            if (isPrimitive(fqn)) {
                return Call.gap("unknown method", null);
            }
            return new Call(null, fqn + "#" + name, false, fqn, false, null, null);
        }
        List<Fn> found = methodsOf(fqn, name, args, false);
        if (found.isEmpty()) {
            found = methodsOf(fqn, name, args, true);
        }
        if (!found.isEmpty()) {
            Fn f = found.get(0);
            return new Call(f, null, generated.contains(f.node()), fqn, thisCall, null, null);
        }
        String lib = libraryAncestor(fqn);
        if (lib != null) {
            return new Call(null, lib + "#" + name, false, fqn, thisCall, null, null);
        }
        return null;
    }

    private Call fromResolved(ResolvedMethodLikeDeclaration r, String receiver, boolean thisCall) {
        Optional<Node> ast = r.toAst();
        if (ast.isPresent()) {
            Fn fn = fns.get(ast.get());
            if (fn != null) {
                return new Call(fn, null, generated.contains(ast.get()), receiver, thisCall, null, null);
            }
            return null; // code we don't analyze (an anonymous class): let the fallback decide
        }
        String owner = r.declaringType().getQualifiedName();
        return new Call(null, owner + "#" + r.getName(), false, receiver, false, null, null);
    }

    private Call creation(ObjectCreationExpr o) {
        if (o.getAnonymousClassBody().isPresent()) {
            return null;
        }
        String fqn = typeName(o.getType(), o);
        try {
            ResolvedConstructorDeclaration r = o.resolve();
            Optional<Node> ast = r.toAst();
            if (ast.isPresent()) {
                Fn fn = fns.get(ast.get());
                if (fn != null) {
                    return new Call(fn, null, generated.contains(ast.get()), fqn, false, null, null);
                }
            } else if (fqn != null && !types.containsKey(fqn)) {
                return new Call(null, fqn + "#<init>", false, fqn, false, null, null);
            }
        } catch (Throwable ignored) {
            // fall back below
        }
        if (fqn == null) {
            return Call.gap("unknown type", null);
        }
        TypeDeclaration<?> td = types.get(fqn);
        if (td == null) {
            return new Call(null, fqn + "#<init>", false, fqn, false, null, null);
        }
        int args = o.getArguments().size();
        for (ConstructorDeclaration c : td.getConstructors()) {
            if (c.getParameters().size() == args) {
                Fn fn = fns.get(c);
                return fn == null ? null : new Call(fn, null, generated.contains(c), fqn, false, null, null);
            }
        }
        return null; // the default constructor: no code of its own
    }

    private Call methodReference(MethodReferenceExpr ref) {
        String name = ref.getIdentifier();
        if (name.equals("new")) {
            return null;
        }
        boolean thisCall = ref.getScope() instanceof ThisExpr;
        try {
            ResolvedMethodDeclaration r = ref.resolve();
            Call known = fromResolved(r, null, thisCall);
            if (known != null) {
                return known;
            }
        } catch (Throwable ignored) {
            // fall back below
        }
        Expression scope = ref.getScope();
        // `mapper::toEntity` parses as a type named `mapper`: a variable first, if there is one
        Var held = scope instanceof TypeExpr te && te.getType().isClassOrInterfaceType()
                && te.getType().asClassOrInterfaceType().getScope().isEmpty()
                ? variable(te.getType().asClassOrInterfaceType().getNameAsString(), ref) : null;
        String owner;
        if (held != null) {
            Typed t = typeOfVar(held);
            if (t.fqn() == null) {
                return Call.gap(t.reason() != null ? t.reason() : "unknown type", t.library());
            }
            owner = t.fqn();
        } else if (scope instanceof TypeExpr te) {
            owner = typeName(te.getType(), ref);
        } else if (scope instanceof ThisExpr) {
            TypeDeclaration<?> t = enclosingType(ref);
            owner = t == null ? null : t.getFullyQualifiedName().orElse(null);
        } else {
            String cls = classOf(scope);
            owner = cls != null ? cls : typeOf(scope).fqn();
            if (owner == null) {
                Typed t = typeOf(scope);
                return Call.gap(t.reason() != null ? t.reason() : "unknown type", t.library());
            }
        }
        if (owner == null) {
            return Call.gap("unknown type", null);
        }
        if (!types.containsKey(owner)) {
            return new Call(null, owner + "#" + name, false, owner, false, null, null);
        }
        List<Fn> found = methodsOf(owner, name, 0, true);
        if (!found.isEmpty()) {
            Fn f = found.get(0);
            return new Call(f, null, generated.contains(f.node()), owner, thisCall, null, null);
        }
        String lib = libraryAncestor(owner);
        return lib != null ? new Call(null, lib + "#" + name, false, owner, false, null, null)
                : Call.gap("unknown method", null);
    }

    private String receiverType(Expression scope, Node at) {
        if (scope == null || scope instanceof ThisExpr) {
            TypeDeclaration<?> t = enclosingType(at);
            return t == null ? null : t.getFullyQualifiedName().orElse(null);
        }
        if (scope instanceof SuperExpr) {
            return null;
        }
        String cls = classOf(scope);
        return cls != null ? cls : typeOf(scope).fqn();
    }

    private String staticImport(Node at, String name) {
        CompilationUnit cu = at.findCompilationUnit().orElse(null);
        if (cu == null) {
            return null;
        }
        for (ImportDeclaration imp : cu.getImports()) {
            if (!imp.isStatic()) {
                continue;
            }
            String n = imp.getNameAsString();
            if (!imp.isAsterisk() && n.endsWith("." + name)) {
                return n.substring(0, n.length() - name.length() - 1);
            }
            if (imp.isAsterisk() && types.containsKey(n)
                    && !types.get(n).getMethodsByName(name).isEmpty()) {
                return n;
            }
        }
        return null;
    }

    private static boolean conditional(Node site, Node stop) {
        Node child = site;
        for (Node p = site.getParentNode().orElse(null); p != null && p != stop; p = p.getParentNode().orElse(null)) {
            if (p instanceof IfStmt || p instanceof ConditionalExpr || p instanceof SwitchStmt
                    || p instanceof SwitchExpr || p instanceof CatchClause
                    || (p instanceof TryStmt t && t.getTryBlock() != child)) {
                return true;
            }
            if (p instanceof BinaryExpr b && (b.getOperator() == BinaryExpr.Operator.AND
                    || b.getOperator() == BinaryExpr.Operator.OR) && b.getLeft() != child) {
                return true;
            }
            child = p;
        }
        return false;
    }

    private static boolean inLoop(Node site, Node stop) {
        for (Node p = site.getParentNode().orElse(null); p != null && p != stop; p = p.getParentNode().orElse(null)) {
            if (p instanceof ForStmt || p instanceof ForEachStmt || p instanceof WhileStmt || p instanceof DoStmt) {
                return true;
            }
            if ((p instanceof LambdaExpr || p instanceof MethodReferenceExpr)
                    && p.getParentNode().orElse(null) instanceof MethodCallExpr call
                    && PER_ELEMENT.contains(call.getNameAsString())) {
                return true;
            }
        }
        return false;
    }

    // ------------------------------------------------------------------- types

    Typed typeOf(Expression e) {
        Typed cached = typeCache.get(e);
        if (cached != null) {
            return cached;
        }
        if (!busyTypes.add(e)) {
            return Typed.unknown("unknown type", null);
        }
        Typed t;
        try {
            t = solvedType(e);
            if (t == null) {
                t = fallbackType(e);
            }
        } catch (StackOverflowError ex) {
            t = Typed.unknown("unknown type", null);
        } finally {
            busyTypes.remove(e);
        }
        typeCache.put(e, t);
        return t;
    }

    private Typed solvedType(Expression e) {
        if (e instanceof LambdaExpr || e instanceof MethodReferenceExpr) {
            return null;
        }
        try {
            ResolvedType r = e.calculateResolvedType();
            if (r.isReferenceType()) {
                return Typed.of(r.asReferenceType().getQualifiedName());
            }
            if (r.isPrimitive() || r.isArray()) {
                return Typed.of(r.describe());
            }
        } catch (Throwable ignored) {
            // fall back
        }
        return null;
    }

    private Typed fallbackType(Expression e) {
        if (e instanceof EnclosedExpr en) {
            return typeOf(en.getInner());
        }
        if (e instanceof CastExpr c) {
            return named(typeName(c.getType(), c));
        }
        if (e instanceof StringLiteralExpr || e instanceof TextBlockLiteralExpr) {
            return Typed.of("java.lang.String");
        }
        if (e instanceof ClassExpr) {
            return Typed.of("java.lang.Class");
        }
        if (e instanceof ThisExpr) {
            TypeDeclaration<?> t = enclosingType(e);
            return named(t == null ? null : t.getFullyQualifiedName().orElse(null));
        }
        if (e instanceof ObjectCreationExpr o) {
            return named(typeName(o.getType(), o));
        }
        if (e instanceof ConditionalExpr c) {
            Typed t = typeOf(c.getThenExpr());
            return t.fqn() != null ? t : typeOf(c.getElseExpr());
        }
        if (e instanceof AssignExpr a) {
            return typeOf(a.getTarget());
        }
        if (e instanceof BinaryExpr b && b.getOperator() == BinaryExpr.Operator.PLUS) {
            Typed l = typeOf(b.getLeft());
            if ("java.lang.String".equals(l.fqn()) || "java.lang.String".equals(typeOf(b.getRight()).fqn())) {
                return Typed.of("java.lang.String");
            }
            return Typed.unknown("unknown type", null);
        }
        if (e instanceof NameExpr n) {
            Var v = variable(n.getNameAsString(), n);
            if (v == null) {
                return Typed.unknown("unknown type", null);
            }
            return typeOfVar(v);
        }
        if (e instanceof FieldAccessExpr f) {
            Expression scope = f.getScope();
            String owner = classOf(scope);
            if (owner == null) {
                Typed t = typeOf(scope);
                if (t.fqn() == null) {
                    return t;
                }
                owner = t.fqn();
            }
            if (!types.containsKey(owner)) {
                return libraryGap(owner);
            }
            Var v = field(owner, f.getNameAsString());
            return v == null ? Typed.unknown("unknown type", null) : typeOfVar(v);
        }
        if (e instanceof MethodCallExpr m) {
            Call c = callOf(m);
            if (c == null) {
                return Typed.unknown("unknown type", null);
            }
            if (c.fn() != null) {
                Node node = c.fn().node();
                if (node instanceof MethodDeclaration md) {
                    return named(typeName(md.getType(), md));
                }
                return Typed.unknown("unknown type", null);
            }
            if (c.library() != null) {
                String owner = c.library().substring(0, c.library().indexOf('#'));
                return libraryGap(owner);
            }
            return Typed.unknown(c.reason(), c.libraryOfGap());
        }
        return Typed.unknown("unknown type", null);
    }

    /** A type that comes from a library: unknown because no JAR is available. The JDK always
     *  is, so a JDK type that still isn't known is generics the solver couldn't infer. */
    private Typed libraryGap(String owner) {
        String cls = owner.contains("#") ? owner.substring(0, owner.indexOf('#')) : owner;
        if (cls.startsWith("java.") || (cls.startsWith("javax.") && jdkHas(cls))) {
            return Typed.unknown("unknown type", null);
        }
        return Typed.unknown("library type", libraryOf(cls));
    }

    private Typed named(String fqn) {
        return fqn == null ? Typed.unknown("unknown type", null) : Typed.of(fqn);
    }

    private Typed typeOfVar(Var v) {
        if (v.type() == null || v.type().isVarType()) {
            if (v.init() != null) {
                return typeOf(v.init());
            }
            if (v.decl() instanceof Parameter p && p.getParentNode().orElse(null) instanceof LambdaExpr l) {
                return lambdaParameter(l, p);
            }
            return Typed.unknown("unknown type", null);
        }
        String context = v.owner();
        Node at = v.decl() != null ? v.decl() : (context != null ? types.get(context) : null);
        return named(at == null ? null : typeName(v.type(), at));
    }

    // ------------------------------------------------- element types (streams)

    /** A type as declared, with the node it's declared at (where its names resolve). */
    record TypeRef(Type type, Node at) {}

    /** Stream and collection operations whose elements are the receiver's elements. */
    private static final Set<String> SAME_ELEMENTS = Set.of(
            "stream", "parallelStream", "filter", "peek", "sorted", "distinct", "limit", "skip",
            "takeWhile", "dropWhile", "toList", "iterator", "unordered", "sequential", "parallel",
            "onClose", "stream()", "subList", "values", "keySet", "doOnNext", "filterWhen");

    /** An untyped lambda parameter in `xs.stream().filter(x -> …)`: the element type of the
     *  stream it's applied to, when that can be told from declared types. */
    private Typed lambdaParameter(LambdaExpr l, Parameter p) {
        if (!(l.getParentNode().orElse(null) instanceof MethodCallExpr call)) {
            return Typed.unknown("unknown type", null);
        }
        String name = call.getNameAsString();
        if (call.getScope().isPresent() && l.getParameters().size() == 1
                && (PER_ELEMENT.contains(name) || SAME_ELEMENTS.contains(name))) {
            TypeRef element = elementOf(call.getScope().get(), 0);
            String fqn = element == null ? null : typeName(element.type(), element.at());
            if (fqn != null) {
                return Typed.of(fqn);
            }
            Typed receiver = typeOf(call.getScope().get());
            if (receiver.fqn() == null && "library type".equals(receiver.reason())) {
                return receiver;
            }
        }
        // A lambda handed to a library method: its parameter's type is the library's to say
        Call c = callOf(call);
        if (c != null && c.library() != null) {
            return libraryGap(c.library());
        }
        if (c != null && c.fn() == null && "library type".equals(c.reason())) {
            return Typed.unknown("library type", c.libraryOfGap());
        }
        return Typed.unknown("unknown type", null);
    }

    /** The element type of a collection, stream, or Optional expression. */
    private TypeRef elementOf(Expression e, int depth) {
        if (depth > 12) {
            return null;
        }
        if (e instanceof MethodCallExpr m) {
            String name = m.getNameAsString();
            Expression scope = m.getScope().orElse(null);
            if (scope != null && SAME_ELEMENTS.contains(name)) {
                return elementOf(scope, depth + 1);
            }
            if (scope != null && name.equals("flatMap") && m.getArguments().size() == 1
                    && m.getArgument(0) instanceof MethodReferenceExpr ref && ref.getIdentifier().equals("stream")) {
                TypeRef outer = elementOf(scope, depth + 1);
                return outer == null ? null : argument(outer);
            }
            if (scope != null && name.equals("map") && m.getArguments().size() == 1) {
                Expression f = m.getArgument(0);
                Call c = f instanceof MethodReferenceExpr ref ? callOf(ref) : null;
                if (c != null && c.fn() != null && c.fn().node() instanceof MethodDeclaration md) {
                    return new TypeRef(md.getType(), md);
                }
                if (f instanceof LambdaExpr lam && lam.getExpressionBody().isPresent()) {
                    return declared(lam.getExpressionBody().get(), depth + 1);
                }
                return null;
            }
            if (scope instanceof NameExpr n && Set.of("Optional", "Stream", "List", "Set", "Arrays", "Collections")
                    .contains(n.getNameAsString()) && !m.getArguments().isEmpty()
                    && Set.of("of", "ofNullable", "asList", "singletonList", "singleton").contains(name)) {
                return declared(m.getArgument(0), depth + 1);
            }
        }
        TypeRef declared = declared(e, depth + 1);
        return declared == null ? null : argument(declared);
    }

    /** The type argument of a declared container type: `List<User>` → User. */
    private static TypeRef argument(TypeRef container) {
        if (!container.type().isClassOrInterfaceType()) {
            return null;
        }
        ClassOrInterfaceType c = container.type().asClassOrInterfaceType();
        if (c.getTypeArguments().isEmpty() || c.getTypeArguments().get().isEmpty()) {
            return null;
        }
        NodeList<Type> args = c.getTypeArguments().get();
        Type last = args.get(args.size() - 1); // Map<K, V> → V
        if (last.isWildcardType()) {
            last = last.asWildcardType().getExtendedType().map(t -> (Type) t).orElse(null);
        }
        return last == null ? null : new TypeRef(last, container.at());
    }

    /** The type an expression is declared with, generics included. */
    private TypeRef declared(Expression e, int depth) {
        if (depth > 12) {
            return null;
        }
        if (e instanceof EnclosedExpr en) {
            return declared(en.getInner(), depth + 1);
        }
        if (e instanceof NameExpr || e instanceof FieldAccessExpr) {
            Var v = e instanceof NameExpr n ? variable(n.getNameAsString(), n) : fieldOf((FieldAccessExpr) e);
            if (v == null) {
                return null;
            }
            if (v.type() != null && !v.type().isVarType()) {
                Node at = v.decl() != null ? v.decl() : types.get(v.owner());
                return at == null ? null : new TypeRef(v.type(), at);
            }
            return v.init() == null ? null : declared(v.init(), depth + 1);
        }
        if (e instanceof MethodCallExpr m) {
            Call c = callOf(m);
            if (c != null && c.fn() != null && c.fn().node() instanceof MethodDeclaration md) {
                return new TypeRef(md.getType(), md);
            }
            return null;
        }
        if (e instanceof ObjectCreationExpr o) {
            return new TypeRef(o.getType(), o);
        }
        if (e instanceof CastExpr c) {
            return new TypeRef(c.getType(), c);
        }
        return null;
    }

    /** If the expression names a class (a static call's receiver, `Foo.class`), its name. */
    String classOf(Expression e) {
        if (e instanceof ClassExpr c) {
            return typeName(c.getType(), c);
        }
        if (e instanceof TypeExpr t) {
            return typeName(t.getType(), t);
        }
        if (e instanceof NameExpr n) {
            if (variable(n.getNameAsString(), n) != null) {
                return null;
            }
            return typeName(n.getNameAsString(), n);
        }
        if (e instanceof FieldAccessExpr f) {
            if (classOf(f.getScope()) != null) {
                String owner = classOf(f.getScope());
                String nested = owner + "." + f.getNameAsString();
                if (types.containsKey(nested)) {
                    return nested;
                }
                if (types.containsKey(owner) || field(owner, f.getNameAsString()) != null) {
                    return null; // a static field
                }
                return Character.isUpperCase(f.getNameAsString().charAt(0)) && jdkHas(nested) ? nested : null;
            }
            String dotted = f.toString();
            if (Character.isLowerCase(dotted.charAt(0)) && variable(dotted.split("\\.")[0], f) == null) {
                String last = f.getNameAsString();
                if (Character.isUpperCase(last.charAt(0))) {
                    return dotted; // a fully qualified class name
                }
            }
        }
        return null;
    }

    /** A type name as written, resolved where it's used: nested and enclosing types, imports,
     *  the same package, java.lang, then on-demand imports. Null when it can't be told. */
    String typeName(Type t, Node at) {
        if (t == null) {
            return null;
        }
        if (t.isPrimitiveType()) {
            return t.asString();
        }
        if (t.isArrayType()) {
            String c = typeName(t.asArrayType().getComponentType(), at);
            return c == null ? null : c + "[]";
        }
        if (t.isClassOrInterfaceType()) {
            return typeName(t.asClassOrInterfaceType().getNameWithScope(), at);
        }
        return null;
    }

    String typeName(String name, Node at) {
        if (name == null || name.isEmpty()) {
            return null;
        }
        int dot = name.indexOf('.');
        String first = dot < 0 ? name : name.substring(0, dot);
        String rest = dot < 0 ? "" : name.substring(dot);
        for (Node n = at; n != null; n = n.getParentNode().orElse(null)) {
            if (n instanceof TypeDeclaration<?> td) {
                String fq = td.getFullyQualifiedName().orElse(null);
                if (fq == null) {
                    continue;
                }
                if (td.getNameAsString().equals(first)) {
                    return fq + rest;
                }
                if (types.containsKey(fq + "." + first)) {
                    return fq + "." + first + rest;
                }
                for (String a : ancestors(fq)) {
                    if (types.containsKey(a + "." + first)) {
                        return a + "." + first + rest;
                    }
                }
            }
        }
        CompilationUnit cu = at.findCompilationUnit().orElse(null);
        if (cu != null) {
            for (ImportDeclaration imp : cu.getImports()) {
                if (!imp.isStatic() && !imp.isAsterisk() && imp.getName().getIdentifier().equals(first)) {
                    return imp.getNameAsString() + rest;
                }
            }
            String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
            String same = pkg.isEmpty() ? first : pkg + "." + first;
            if (types.containsKey(same)) {
                return same + rest;
            }
            if (jdkHas("java.lang." + first)) {
                return "java.lang." + first + rest;
            }
            List<String> libraryPackages = new ArrayList<>();
            for (ImportDeclaration imp : cu.getImports()) {
                if (!imp.isStatic() && imp.isAsterisk()) {
                    String cand = imp.getNameAsString() + "." + first;
                    if (types.containsKey(cand) || jdkHas(cand)) {
                        return cand + rest;
                    }
                    String imported = imp.getNameAsString();
                    if (!imported.startsWith("java.") && !imported.startsWith("javax.")
                            && !types.containsKey(imported)) {
                        libraryPackages.add(imported);
                    }
                }
            }
            // `import org.springframework.web.client.*;`: a class name found nowhere else can only
            // come from the one library package imported on demand (when the code compiles)
            if (libraryPackages.size() == 1 && Character.isUpperCase(first.charAt(0))) {
                return libraryPackages.get(0) + "." + first + rest;
            }
        }
        if (dot > 0 && Character.isLowerCase(first.charAt(0))) {
            return name; // already fully qualified
        }
        return null;
    }

    private boolean jdkHas(String fqn) {
        return jdk.computeIfAbsent(fqn, k -> {
            try {
                Class.forName(k, false, ClassLoader.getPlatformClassLoader());
                return true;
            } catch (Throwable e) {
                try {
                    Class.forName(k, false, Analyzer.class.getClassLoader());
                    return k.startsWith("java.") || k.startsWith("javax.");
                } catch (Throwable e2) {
                    return false;
                }
            }
        });
    }

    private static boolean isPrimitive(String t) {
        return Set.of("int", "long", "short", "byte", "char", "boolean", "float", "double", "void").contains(t)
                || t.endsWith("[]");
    }

    // --------------------------------------------------------------- variables

    /** The variable `name` refers to at `at`: a lambda or catch parameter, a local, a method
     *  parameter, a field of an enclosing type or its ancestors, or a static import. */
    Var variable(String name, Node at) {
        Node child = at;
        for (Node p = at.getParentNode().orElse(null); p != null; child = p, p = p.getParentNode().orElse(null)) {
            if (p instanceof LambdaExpr l) {
                for (Parameter param : l.getParameters()) {
                    if (param.getNameAsString().equals(name)) {
                        return new Var(name, param.getType().isUnknownType() ? null : param.getType(), null, param, null, false, param);
                    }
                }
            } else if (p instanceof CatchClause c && c.getParameter().getNameAsString().equals(name)) {
                return new Var(name, c.getParameter().getType(), null, c.getParameter(), null, false, c.getParameter());
            } else if (p instanceof ForEachStmt f) {
                for (VariableDeclarator v : f.getVariable().getVariables()) {
                    if (v.getNameAsString().equals(name)) {
                        return new Var(name, v.getType(), null, v, null, false, null);
                    }
                }
            } else if (p instanceof ForStmt f) {
                for (Expression init : f.getInitialization()) {
                    Var v = declared(init, name);
                    if (v != null) {
                        return v;
                    }
                }
            } else if (p instanceof TryStmt t) {
                for (Expression res : t.getResources()) {
                    Var v = declared(res, name);
                    if (v != null) {
                        return v;
                    }
                }
            } else if (p instanceof BlockStmt b) {
                Var found = null;
                for (Statement s : b.getStatements()) {
                    if (s == child) {
                        break;
                    }
                    if (s instanceof ExpressionStmt es) {
                        Var v = declared(es.getExpression(), name);
                        if (v != null) {
                            found = v;
                        }
                    }
                }
                if (found != null) {
                    return found;
                }
            } else if (p instanceof CallableDeclaration<?> c) {
                for (Parameter param : c.getParameters()) {
                    if (param.getNameAsString().equals(name)) {
                        return new Var(name, param.getType(), null, param, null, false, param);
                    }
                }
            } else if (p instanceof TypeDeclaration<?> td) {
                String fqn = td.getFullyQualifiedName().orElse(null);
                Var v = fqn == null ? null : field(fqn, name);
                if (v != null) {
                    return v;
                }
            }
        }
        CompilationUnit cu = at.findCompilationUnit().orElse(null);
        if (cu != null) {
            for (ImportDeclaration imp : cu.getImports()) {
                if (imp.isStatic() && !imp.isAsterisk() && imp.getName().getIdentifier().equals(name)) {
                    String owner = imp.getName().getQualifier().map(Object::toString).orElse(null);
                    Var v = owner == null ? null : field(owner, name);
                    if (v != null) {
                        return v;
                    }
                }
            }
        }
        return null;
    }

    private static Var declared(Expression e, String name) {
        if (e instanceof VariableDeclarationExpr vd) {
            for (VariableDeclarator v : vd.getVariables()) {
                if (v.getNameAsString().equals(name)) {
                    return new Var(name, v.getType(), v.getInitializer().orElse(null), v, null, false, null);
                }
            }
        }
        return null;
    }

    /** A field of a repository type or its repository ancestors (enum constants included). */
    Var field(String owner, String name) {
        List<String> chain = new ArrayList<>();
        chain.add(owner);
        chain.addAll(ancestors(owner));
        for (String t : chain) {
            TypeDeclaration<?> td = types.get(t);
            if (td == null) {
                continue;
            }
            for (FieldDeclaration f : td.getFields()) {
                for (VariableDeclarator v : f.getVariables()) {
                    if (v.getNameAsString().equals(name)) {
                        return new Var(name, v.getType(), v.getInitializer().orElse(null), v, t, true, null);
                    }
                }
            }
            if (td instanceof EnumDeclaration en && en.getEntries().stream().anyMatch(c -> c.getNameAsString().equals(name))) {
                return new Var(name, new ClassOrInterfaceType(null, td.getNameAsString()), null, td, t, true, null);
            }
            if (td instanceof RecordDeclaration rd) {
                for (Parameter p : rd.getParameters()) {
                    if (p.getNameAsString().equals(name)) {
                        return new Var(name, p.getType(), null, p, t, true, p);
                    }
                }
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ values

    /** The constant an expression holds, or null. Spring placeholders (`${key}`) are kept for
     *  the engine to fill in from config, since that's where the property map lives (D70). */
    Object valueOf(Expression e, int depth) {
        if (depth > 8) {
            return null;
        }
        if (e instanceof StringLiteralExpr s) {
            return s.asString();
        }
        if (e instanceof TextBlockLiteralExpr t) {
            return t.asString();
        }
        if (e instanceof IntegerLiteralExpr i) {
            try {
                return i.asNumber();
            } catch (RuntimeException ex) {
                return null;
            }
        }
        if (e instanceof LongLiteralExpr l) {
            try {
                return l.asNumber();
            } catch (RuntimeException ex) {
                return null;
            }
        }
        if (e instanceof DoubleLiteralExpr d) {
            return d.asDouble();
        }
        if (e instanceof BooleanLiteralExpr b) {
            return b.getValue();
        }
        if (e instanceof CharLiteralExpr c) {
            return String.valueOf(c.asChar());
        }
        if (e instanceof EnclosedExpr en) {
            return valueOf(en.getInner(), depth + 1);
        }
        if (e instanceof CastExpr c) {
            return valueOf(c.getExpression(), depth + 1);
        }
        if (e instanceof BinaryExpr b && b.getOperator() == BinaryExpr.Operator.PLUS) {
            Object l = valueOf(b.getLeft(), depth + 1);
            Object r = valueOf(b.getRight(), depth + 1);
            if (l == null || r == null) {
                return null;
            }
            if (l instanceof String || r instanceof String) {
                return text(l) + text(r);
            }
            if (l instanceof Number a && r instanceof Number c) {
                return a instanceof Double || c instanceof Double ? a.doubleValue() + c.doubleValue()
                        : a.longValue() + c.longValue();
            }
            return null;
        }
        if (e instanceof NameExpr || e instanceof FieldAccessExpr) {
            Var v = e instanceof NameExpr n ? variable(n.getNameAsString(), n) : fieldOf((FieldAccessExpr) e);
            return v == null ? null : valueOfVar(v, depth);
        }
        if (e instanceof MethodCallExpr m) {
            return formatted(m, depth, false);
        }
        return null;
    }

    private Object valueOfVar(Var v, int depth) {
        if (v.param() != null) {
            return annotationString(v.param(), "Value");
        }
        if (v.decl() instanceof VariableDeclarator vd && v.field()) {
            FieldDeclaration f = vd.findAncestor(FieldDeclaration.class).orElse(null);
            String prop = f == null ? null : annotationString(f, "Value");
            if (prop != null) {
                return prop;
            }
            if (v.init() != null) {
                return valueOf(v.init(), depth + 1);
            }
            Expression assigned = assignedInConstructor(v);
            return assigned == null ? null : valueOf(assigned, depth + 1);
        }
        if (v.init() != null && !reassigned(v)) {
            return valueOf(v.init(), depth + 1);
        }
        return null;
    }

    /** A string with its runtime parts as named placeholders: `{baseUrl}/users/{id}`. */
    String templateOf(Expression e, int depth) {
        if (depth > 8) {
            return null;
        }
        Object v = valueOf(e, depth);
        if (v instanceof String || v instanceof Number) {
            return text(v);
        }
        if (e instanceof EnclosedExpr en) {
            return templateOf(en.getInner(), depth + 1);
        }
        if (e instanceof BinaryExpr b && b.getOperator() == BinaryExpr.Operator.PLUS) {
            String l = templateOf(b.getLeft(), depth + 1);
            String r = templateOf(b.getRight(), depth + 1);
            if (l == null && r == null) {
                return null;
            }
            return (l != null ? l : "{" + placeholder(b.getLeft()) + "}") + (r != null ? r : "{" + placeholder(b.getRight()) + "}");
        }
        if (e instanceof NameExpr || e instanceof FieldAccessExpr) {
            Var var = e instanceof NameExpr n ? variable(n.getNameAsString(), n) : fieldOf((FieldAccessExpr) e);
            if (var == null) {
                return null;
            }
            if (var.field() && var.init() == null) {
                Expression assigned = assignedInConstructor(var);
                if (assigned != null) {
                    return templateOf(assigned, depth + 1);
                }
            }
            if (!var.field() && var.param() == null && var.init() != null) {
                return templateOf(var.init(), depth + 1);
            }
            return null;
        }
        if (e instanceof MethodCallExpr m) {
            Object f = formatted(m, depth, true);
            return f instanceof String s ? s : null;
        }
        if (e instanceof ConditionalExpr c) {
            String a = templateOf(c.getThenExpr(), depth + 1);
            String b = templateOf(c.getElseExpr(), depth + 1);
            return a != null && a.equals(b) ? a : (a != null && b == null ? a : (b != null && a == null ? b : null));
        }
        return null;
    }

    /** `String.format(fmt, args)`, `fmt.formatted(args)`, `a.concat(b)`. */
    private Object formatted(MethodCallExpr m, int depth, boolean template) {
        String name = m.getNameAsString();
        Expression scope = m.getScope().orElse(null);
        NodeList<Expression> args = m.getArguments();
        String fmt = null;
        List<Expression> values = new ArrayList<>();
        if (name.equals("format") && scope instanceof NameExpr n && n.getNameAsString().equals("String") && !args.isEmpty()) {
            Object f = valueOf(args.get(0), depth + 1);
            fmt = f instanceof String s ? s : null;
            values.addAll(args.subList(1, args.size()));
        } else if (name.equals("formatted") && scope != null) {
            Object f = valueOf(scope, depth + 1);
            fmt = f instanceof String s ? s : null;
            values.addAll(args);
        } else if (name.equals("concat") && scope != null && args.size() == 1) {
            String a = template ? templateOf(scope, depth + 1) : asText(valueOf(scope, depth + 1));
            String b = template ? templateOf(args.get(0), depth + 1) : asText(valueOf(args.get(0), depth + 1));
            if (a == null || b == null) {
                return template && (a != null || b != null)
                        ? (a != null ? a : "{" + placeholder(scope) + "}") + (b != null ? b : "{" + placeholder(args.get(0)) + "}")
                        : null;
            }
            return a + b;
        }
        if (fmt == null) {
            return null;
        }
        StringBuilder out = new StringBuilder();
        Matcher mt = FORMAT.matcher(fmt);
        int last = 0;
        int i = 0;
        while (mt.find()) {
            out.append(fmt, last, mt.start());
            Expression arg = i < values.size() ? values.get(i) : null;
            i++;
            String part = arg == null ? null : (template ? templateOf(arg, depth + 1) : asText(valueOf(arg, depth + 1)));
            if (part == null) {
                if (!template) {
                    return null;
                }
                part = "{" + (arg == null ? "?" : placeholder(arg)) + "}";
            }
            out.append(part);
            last = mt.end();
        }
        out.append(fmt.substring(last));
        return out.toString();
    }

    private Var fieldOf(FieldAccessExpr f) {
        String owner = classOf(f.getScope());
        if (owner == null) {
            if (f.getScope() instanceof ThisExpr) {
                TypeDeclaration<?> t = enclosingType(f);
                owner = t == null ? null : t.getFullyQualifiedName().orElse(null);
            } else {
                owner = typeOf(f.getScope()).fqn();
            }
        }
        return owner == null ? null : field(owner, f.getNameAsString());
    }

    /** `this.url = url;` in a constructor, when the field has no initializer. */
    private Expression assignedInConstructor(Var v) {
        TypeDeclaration<?> td = v.owner() == null ? null : types.get(v.owner());
        if (td == null) {
            return null;
        }
        List<Expression> found = new ArrayList<>();
        for (ConstructorDeclaration c : td.getConstructors()) {
            c.getBody().walk(AssignExpr.class, a -> {
                Expression target = a.getTarget();
                boolean match = (target instanceof FieldAccessExpr fa && fa.getScope() instanceof ThisExpr
                        && fa.getNameAsString().equals(v.name()))
                        || (target instanceof NameExpr n && n.getNameAsString().equals(v.name())
                        && c.getParameters().stream().noneMatch(p -> p.getNameAsString().equals(v.name())));
                if (match && a.getOperator() == AssignExpr.Operator.ASSIGN) {
                    found.add(a.getValue());
                }
            });
        }
        return found.size() == 1 ? found.get(0) : null;
    }

    private static boolean reassigned(Var v) {
        if (!(v.decl() instanceof VariableDeclarator vd)) {
            return false;
        }
        Node scope = vd.findAncestor(BlockStmt.class).orElse(null);
        if (scope == null) {
            return false;
        }
        boolean[] found = {false};
        scope.walk(AssignExpr.class, a -> {
            if (a.getTarget() instanceof NameExpr n && n.getNameAsString().equals(v.name())) {
                found[0] = true;
            }
        });
        return found[0];
    }

    static String annotationString(NodeWithAnnotations<?> node, String simple) {
        for (AnnotationExpr a : node.getAnnotations()) {
            String n = a.getNameAsString();
            if (!(n.equals(simple) || n.endsWith("." + simple))) {
                continue;
            }
            Expression value = null;
            if (a instanceof SingleMemberAnnotationExpr s) {
                value = s.getMemberValue();
            } else if (a instanceof NormalAnnotationExpr na) {
                value = na.getPairs().stream().filter(p -> p.getNameAsString().equals("value"))
                        .map(p -> p.getValue()).findFirst().orElse(null);
            }
            if (value instanceof StringLiteralExpr s) {
                return s.asString();
            }
        }
        return null;
    }

    private static String placeholder(Expression e) {
        if (e instanceof NameExpr n) {
            return n.getNameAsString();
        }
        if (e instanceof FieldAccessExpr f) {
            return f.getNameAsString();
        }
        if (e instanceof MethodCallExpr m) {
            String n = m.getNameAsString();
            return n.startsWith("get") && n.length() > 3 ? Character.toLowerCase(n.charAt(3)) + n.substring(4) : n;
        }
        return "?";
    }

    private static String text(Object v) {
        if (v instanceof Double d && d == Math.rint(d)) {
            return String.valueOf(d);
        }
        return String.valueOf(v);
    }

    private static String asText(Object v) {
        return v instanceof String || v instanceof Number ? text(v) : null;
    }

    // ------------------------------------------------------------- expressions

    /** Every expression a rule might capture, with what's known about it. */
    private void expressions(CompilationUnit cu, String file, List<Map<String, Object>> out) {
        cu.walk(Expression.class, e -> {
            if (!(e instanceof NameExpr || e instanceof FieldAccessExpr || e instanceof MethodCallExpr
                    || e instanceof ObjectCreationExpr || e instanceof BinaryExpr || e instanceof ClassExpr
                    || e instanceof MethodReferenceExpr)) {
                return;
            }
            if (e instanceof BinaryExpr b && b.getOperator() != BinaryExpr.Operator.PLUS) {
                return;
            }
            Range r = e.getRange().orElse(null);
            if (r == null || isGenerated(e)) {
                return;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("file", file);
            m.put("sl", r.begin.line);
            m.put("sc", r.begin.column);
            m.put("el", r.end.line);
            m.put("ec", r.end.column);
            try {
                String cls = classOf(e);
                if (cls != null) {
                    m.put("class", cls);
                    m.put("symbol", cls);
                } else {
                    String sym = symbolOf(e);
                    if (sym != null) {
                        m.put("symbol", sym);
                    }
                    if (!(e instanceof MethodReferenceExpr)) {
                        Typed t = typeOf(e);
                        if (t.fqn() != null) {
                            m.put("type", t.fqn());
                        }
                    }
                    Object v = valueOf(e, 0);
                    if (v != null) {
                        m.put("value", v);
                    } else {
                        String tpl = templateOf(e, 0);
                        if (tpl != null) {
                            m.put("template", tpl);
                        }
                    }
                }
            } catch (Throwable ex) {
                // a question the solver can't answer: leave it unknown
                if (DEBUG) {
                    System.err.println(file + ":" + r.begin.line + " " + e + ": " + ex);
                }
            }
            if (m.size() > 5) {
                out.add(m);
            }
        });
    }

    /** What an expression refers to: a field (`Owner.name`), a local, or the method a call runs. */
    private String symbolOf(Expression e) {
        if (e instanceof NameExpr n) {
            Var v = variable(n.getNameAsString(), n);
            if (v == null) {
                return null;
            }
            if (v.field()) {
                return v.owner() + "." + v.name();
            }
            CallableDeclaration<?> c = n.findAncestor(CallableDeclaration.class).orElse(null);
            Fn fn = c == null ? null : fns.get(c);
            return (fn == null ? "?" : fn.symbol()) + ".<locals>." + v.name();
        }
        if (e instanceof FieldAccessExpr f) {
            Var v = fieldOf(f);
            if (v != null) {
                return v.owner() + "." + v.name();
            }
            String owner = classOf(f.getScope());
            if (owner == null) {
                owner = typeOf(f.getScope()).fqn();
            }
            return owner == null ? null : owner + "." + f.getNameAsString();
        }
        if (e instanceof MethodCallExpr || e instanceof ObjectCreationExpr || e instanceof MethodReferenceExpr) {
            Call c = callOf(e);
            if (c == null) {
                return null;
            }
            return c.fn() != null ? c.fn().symbol() : c.library();
        }
        return null;
    }

    private boolean isGenerated(Node n) {
        for (Node p = n; p != null; p = p.getParentNode().orElse(null)) {
            if (generated.contains(p)) {
                return true;
            }
        }
        return false;
    }

    // ----------------------------------------------------------------- helpers

    private static TypeDeclaration<?> enclosingType(Node n) {
        return n.findAncestor(TypeDeclaration.class).orElse(null);
    }

    private static TypeDeclaration<?> outerType(TypeDeclaration<?> t) {
        return t.getParentNode().flatMap(p -> p.findAncestor(TypeDeclaration.class)).orElse(null);
    }

    /** The library a fully qualified name belongs to, by package: `org.springframework.web`,
     *  `com.fasterxml.jackson`, `reactor.core`, `lombok`. */
    static String libraryOf(String fqn) {
        if (fqn == null) {
            return null;
        }
        String owner = fqn.contains("#") ? fqn.substring(0, fqn.indexOf('#')) : fqn;
        List<String> pkg = new ArrayList<>();
        for (String part : owner.split("\\.")) {
            if (part.isEmpty() || Character.isUpperCase(part.charAt(0))) {
                break;
            }
            pkg.add(part);
        }
        if (pkg.isEmpty()) {
            return owner;
        }
        Set<String> domains = Set.of("com", "org", "io", "net", "dev", "de", "uk", "co", "me", "ch", "fr", "nl", "app");
        int n = domains.contains(pkg.get(0)) ? 3 : 2;
        return String.join(".", pkg.subList(0, Math.min(n, pkg.size())));
    }

    private static String text(Node site) {
        String s = site.toString().replaceAll("\\s+", " ");
        if (site instanceof MethodCallExpr m) {
            String scope = m.getScope().map(x -> x.toString().replaceAll("\\s+", " ")).orElse("");
            if (scope.length() > 40) {
                scope = "…" + scope.substring(scope.length() - 39);
            }
            s = (scope.isEmpty() ? "" : scope + ".") + m.getNameAsString() + "(" + (m.getArguments().isEmpty() ? "" : "…") + ")";
        }
        return s.length() > 80 ? s.substring(0, 79) + "…" : s;
    }

    private static String erase(String t) {
        int lt = t.indexOf('<');
        return lt < 0 ? t : t.substring(0, lt);
    }

    private static String hash(Node n) {
        DefaultPrinterConfiguration cfg = new DefaultPrinterConfiguration();
        cfg.removeOption(new DefaultConfigurationOption(DefaultPrinterConfiguration.ConfigOption.PRINT_COMMENTS));
        cfg.removeOption(new DefaultConfigurationOption(DefaultPrinterConfiguration.ConfigOption.PRINT_JAVADOC));
        try {
            byte[] d = MessageDigest.getInstance("SHA-1").digest(n.toString(cfg).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(d).substring(0, 12);
        } catch (NoSuchAlgorithmException e) {
            return "";
        }
    }

    /** An insertion-ordered map keyed by identity. */
    private static final class IdentityLinkedMap<K, V> extends java.util.AbstractMap<K, V> {
        private final IdentityHashMap<K, V> map = new IdentityHashMap<>();
        private final List<K> order = new ArrayList<>();

        @Override
        public V put(K key, V value) {
            if (!map.containsKey(key)) {
                order.add(key);
            }
            return map.put(key, value);
        }

        @Override
        public V get(Object key) {
            return map.get(key);
        }

        @Override
        public Set<Map.Entry<K, V>> entrySet() {
            Set<Map.Entry<K, V>> out = new java.util.LinkedHashSet<>();
            for (K k : order) {
                out.add(new java.util.AbstractMap.SimpleImmutableEntry<>(k, map.get(k)));
            }
            return out;
        }
    }

    /** Repository types for the symbol solver, from the parsed (and Lombok-completed) trees, so
     *  every file sees the generated members. */
    private final class RepoTypeSolver implements TypeSolver {
        private TypeSolver parent;
        private final Map<String, ResolvedReferenceTypeDeclaration> cache = new HashMap<>();

        @Override
        public TypeSolver getParent() {
            return parent;
        }

        @Override
        public void setParent(TypeSolver parent) {
            this.parent = parent;
        }

        @Override
        public SymbolReference<ResolvedReferenceTypeDeclaration> tryToSolveType(String name) {
            TypeDeclaration<?> td = types.get(name);
            if (td == null) {
                return SymbolReference.unsolved();
            }
            ResolvedReferenceTypeDeclaration r = cache.computeIfAbsent(name, k -> td.resolve());
            return SymbolReference.solved(r);
        }
    }
}
