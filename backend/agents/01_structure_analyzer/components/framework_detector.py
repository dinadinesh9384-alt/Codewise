from pathlib import PurePosixPath

from ..schemas.repository import TechnologyDetection

FRAMEWORK_PACKAGES = {
    "django": ("Django", "backend framework"), "flask": ("Flask", "backend framework"),
    "fastapi": ("FastAPI", "backend framework"), "pyramid": ("Pyramid", "backend framework"),
    "react": ("React", "frontend framework"), "next": ("Next.js", "frontend framework"),
    "vue": ("Vue", "frontend framework"), "nuxt": ("Nuxt", "frontend framework"),
    "@angular/core": ("Angular", "frontend framework"), "express": ("Express", "backend framework"),
    "@nestjs/core": ("NestJS", "backend framework"), "vite": ("Vite", "build tool"),
    "spring-boot": ("Spring Boot", "backend framework"), "microsoft.aspnetcore.app": ("ASP.NET Core", "backend framework"),
    "laravel/framework": ("Laravel", "backend framework"), "rails": ("Rails", "backend framework"),
    "symfony/framework-bundle": ("Symfony", "backend framework"), "gin-gonic/gin": ("Gin", "backend framework"),
    "gofiber/fiber": ("Fiber", "backend framework"), "actix-web": ("Actix", "backend framework"), "axum": ("Axum", "backend framework"),
}


def detect_frameworks(paths, dependencies, evidence_for, *, include_frameworks=True):
    if not include_frameworks:
        return ()
    found = {}
    for item in dependencies:
        pairs = ((dependency, item.evidence) for dependency in item.dependencies) if hasattr(item, "dependencies") else ((item, item.evidence),)
        for dependency, evidence in pairs:
            evidence = getattr(dependency, "evidence", None) or evidence
            name = dependency.name.casefold()
            key = name if name in FRAMEWORK_PACKAGES else next((candidate for candidate in FRAMEWORK_PACKAGES
                if candidate in name and candidate in {"spring-boot", "microsoft.aspnetcore.app"}), None)
            if key:
                tech, category = FRAMEWORK_PACKAGES[key]
                found.setdefault(tech, (category, "high", f"Declared direct dependency '{dependency.name}'.", (evidence,)))
    pathset = set(paths)
    file_signals = {
        "Dockerfile": ("Docker", "containerization", "Dockerfile is present."),
        "docker-compose.yml": ("Docker Compose", "containerization", "Compose configuration is present."),
        "docker-compose.yaml": ("Docker Compose", "containerization", "Compose configuration is present."),
        "Jenkinsfile": ("Jenkins", "CI/CD", "Jenkins pipeline file is present."),
        "nginx.conf": ("Nginx", "web server", "Nginx configuration is present."),
        "httpd.conf": ("Apache HTTP Server", "web server", "Apache configuration is present."),
        "angular.json": ("Angular", "frontend framework", "Angular workspace configuration is present."),
    }
    for path in sorted(pathset):
        name = PurePosixPath(path).name
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix == ".vue":
            found.setdefault("Vue", ("frontend framework", "high", "Vue single-file component is present.",
                (evidence_for(path, "framework_detector"),)))
        elif suffix == ".svelte":
            found.setdefault("Svelte", ("frontend framework", "high", "Svelte component file is present.",
                (evidence_for(path, "framework_detector"),)))
        if name in file_signals:
            tech, category, explanation = file_signals[name]
            found.setdefault(tech, (category, "high", explanation, (evidence_for(path, "framework_detector"),)))
        if path.startswith(".github/workflows/") and path.lower().endswith((".yml", ".yaml")):
            found.setdefault("GitHub Actions", ("CI/CD", "high", "Workflow file exists under .github/workflows/.",
                (evidence_for(path, "framework_detector"),)))
        if name.startswith("dockerfile"):
            found.setdefault("Docker", ("containerization", "high", "Dockerfile is present.",
                (evidence_for(path, "framework_detector"),)))
        if name.startswith("docker-compose") and name.endswith((".yml", ".yaml")):
            found.setdefault("Docker Compose", ("containerization", "high", "Compose configuration is present.",
                (evidence_for(path, "framework_detector"),)))
        if name.startswith("vite.config."):
            found.setdefault("Vite", ("build tool", "high", "Vite configuration file is present.",
                (evidence_for(path, "framework_detector"),)))
        if name.startswith("next.config."):
            found.setdefault("Next.js", ("frontend framework", "high", "Next.js configuration file is present.",
                (evidence_for(path, "framework_detector"),)))
        if name.endswith((".tf", ".tf.json")):
            found.setdefault("Terraform", ("infrastructure as code", "high", "Terraform configuration file is present.",
                (evidence_for(path, "framework_detector"),)))
        if path.startswith("k8s/") or path.startswith("kubernetes/") or name in {"kustomization.yaml", "kustomization.yml"}:
            found.setdefault("Kubernetes", ("container orchestration", "medium", "Kubernetes-style manifest path is present.",
                (evidence_for(path, "framework_detector"),)))
    return tuple(TechnologyDetection(name=name, category=data[0], confidence=data[1], explanation=data[2], evidence=data[3])
                 for name, data in sorted(found.items()))
