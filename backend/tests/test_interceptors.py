"""Interceptors (D103) end to end on a small Spring service: a request filter runs before
every HTTP flow and reaches the user table through a lambda @Bean; a validator runs only
where Bean Validation runs it (its validation group); request matchers decide who may call
each endpoint. Needs the JVM helper (`make resolver`)."""

import json
import shutil
from textwrap import dedent

import pytest

from steno.config import get_settings
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

pytestmark = pytest.mark.skipif(
    not (get_settings().resolver_jvm_dir / "lib").is_dir() or shutil.which("java") is None,
    reason="the JVM helper isn't built (make resolver)",
)

J = "src/main/java/com/x/"
FILES = {
    "build.gradle": """
        dependencies {
            implementation 'org.springframework.boot:spring-boot-starter-web'
            implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
            implementation 'org.springframework.boot:spring-boot-starter-security'
            implementation 'org.springframework.boot:spring-boot-starter-validation'
        }
    """,
    J + "User.java": """
        package com.x;
        import jakarta.persistence.*;
        @Entity
        @Table(name = "appUser")
        public class User { @Id private Long id; private String name; }
    """,
    J + "UserRepository.java": """
        package com.x;
        import java.util.Optional;
        import org.springframework.data.jpa.repository.JpaRepository;
        public interface UserRepository extends JpaRepository<User, Long> {
            Optional<User> findByName(String name);
        }
    """,
    J + "RegisterGroup.java": "package com.x;\npublic interface RegisterGroup {}\n",
    J + "LoginGroup.java": "package com.x;\npublic interface LoginGroup {}\n",
    J + "UniqueName.java": """
        package com.x;
        public @interface UniqueName { Class<?>[] groups() default {}; }
    """,
    J + "AuthRequest.java": """
        package com.x;
        @UniqueName(groups = RegisterGroup.class)
        public class AuthRequest { private String name; }
    """,
    J + "UniqueNameValidator.java": """
        package com.x;
        import jakarta.validation.ConstraintValidator;
        import jakarta.validation.ConstraintValidatorContext;
        public class UniqueNameValidator implements ConstraintValidator<UniqueName, AuthRequest> {
            @Override
            public boolean isValid(AuthRequest r, ConstraintValidatorContext c) { return true; }
        }
    """,
    J + "AuthConfig.java": """
        package com.x;
        import org.springframework.context.annotation.Bean;
        import org.springframework.security.core.userdetails.UserDetailsService;
        public class AuthConfig {
            private final UserRepository users;
            public AuthConfig(UserRepository users) { this.users = users; }
            @Bean
            public UserDetailsService userDetailsService() {
                return name -> users.findByName(name).orElseThrow();
            }
        }
    """,
    J + "JwtFilter.java": """
        package com.x;
        import jakarta.servlet.FilterChain;
        import jakarta.servlet.http.*;
        import org.springframework.security.core.userdetails.UserDetailsService;
        import org.springframework.web.filter.OncePerRequestFilter;
        public class JwtFilter extends OncePerRequestFilter {
            private final UserDetailsService details;
            public JwtFilter(UserDetailsService details) { this.details = details; }
            @Override
            protected void doFilterInternal(
                    HttpServletRequest q, HttpServletResponse s, FilterChain c) {
                details.loadUserByUsername("x");
            }
        }
    """,
    J + "SecurityConfig.java": """
        package com.x;
        import org.springframework.http.HttpMethod;
        public class SecurityConfig {
            public Object chain(HttpSecurity http) throws Exception {
                http.authorizeHttpRequests((a) -> a
                        .requestMatchers("/auth/**").permitAll()
                        .requestMatchers(HttpMethod.POST, "/admin/jobs").hasAuthority("ADMIN")
                        .anyRequest().authenticated());
                return http.build();
            }
        }
    """,
    J + "AuthController.java": """
        package com.x;
        import org.springframework.validation.annotation.Validated;
        import org.springframework.web.bind.annotation.*;
        @RestController
        @RequestMapping("/auth")
        public class AuthController {
            @PostMapping("/register")
            public void register(@Validated(RegisterGroup.class) @RequestBody AuthRequest r) {}
            @PostMapping("/login")
            public void login(@Validated(LoginGroup.class) @RequestBody AuthRequest r) {}
        }
    """,
    J + "AdminController.java": """
        package com.x;
        import org.springframework.web.bind.annotation.*;
        @RestController
        public class AdminController {
            @PostMapping("/admin/jobs")
            public void run() {}
        }
    """,
}


@pytest.fixture(scope="module")
def flows(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("svc")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(dedent(text).lstrip())
    packs = load_packs(get_settings().rule_packs_dir)
    engine = run_folder(root, [p for p in packs if is_enabled(p, root)])
    plan = build(
        engine.out,
        engine.resolver,
        detect(root, {ep.origin.file for ep in engine.out.entry_points}),
        Context("svc", None, None, 1, None),
        packs=packs,
    )
    out = {}
    for n in plan.nodes.values():
        if n.labels[0] == "Flow":
            reads = {
                plan.nodes[k[2]].props["name"]
                for k in plan.edges
                if k[0] == "READS_FROM" and k[1] == n.id
            }
            out[n.props["name"]] = {
                **n.props,
                "reads": reads,
                "steps": json.loads(n.props["trace"]),
            }
    return out


def _top(flow: dict) -> list[tuple[str, str | None]]:
    return [(t["name"], t.get("interceptor")) for t in flow["steps"] if "." not in t["path"]]


def test_filter_and_validator_run_before_the_handler(flows: dict) -> None:
    assert _top(flows["POST /auth/register"]) == [
        ("doFilterInternal", "filter"),
        ("isValid", "validation"),  # RegisterGroup is active
        ("register", None),
    ]
    # LoginGroup is active: the constraint is only in RegisterGroup
    assert _top(flows["POST /auth/login"]) == [("doFilterInternal", "filter"), ("login", None)]


def test_the_filter_reaches_the_user_table_through_the_lambda_bean(flows: dict) -> None:
    assert "app_user" in flows["POST /admin/jobs"]["reads"]
    assert not flows["POST /admin/jobs"].get("incomplete")
    assert not any(n.startswith(("Framework callback", "Background work")) for n in flows)


def test_access_from_request_matchers(flows: dict) -> None:
    assert flows["POST /auth/register"]["access"] == "public"
    assert flows["POST /admin/jobs"]["access"] == "role:ADMIN"
