from pathlib import PurePosixPath

from ..schemas.repository import ConfigurationFile

CONFIG_NAMES = {
    "pyproject.toml": "Python project configuration", "requirements.txt": "Python dependency configuration",
    "pipfile": "Pipenv dependency configuration", "package.json": "Node package configuration",
    "tsconfig.json": "TypeScript configuration", "angular.json": "Angular workspace configuration",
    "pom.xml": "Maven configuration", "build.gradle": "Gradle configuration", "build.gradle.kts": "Gradle Kotlin configuration",
    "cargo.toml": "Cargo configuration", "go.mod": "Go module configuration", "composer.json": "Composer configuration",
    "gemfile": "Ruby dependencies", "dockerfile": "Docker configuration", "docker-compose.yml": "Docker Compose configuration",
    "docker-compose.yaml": "Docker Compose configuration", "nginx.conf": "Nginx configuration", "jenkinsfile": "Jenkins configuration",
    "makefile": "Make configuration", "pip.conf": "Python package manager configuration",
}


def is_sensitive_path(path):
    name = PurePosixPath(path).name.casefold()
    parts = {part.casefold() for part in PurePosixPath(path).parts}
    if name in {".env.example", ".env.sample", ".env.template", ".env.dist"}:
        return False
    return (name == ".env" or name.startswith(".env.") or name in {".npmrc", ".pypirc", ".netrc", ".git-credentials",
            "id_rsa", "id_ed25519", "credentials"} or (".docker" in parts and name == "config.json")
            or ".ssh" in parts or "token" in name or name.startswith("auth")
            or name.startswith("appsettings")
            or name.endswith((".pem", ".key", ".p8", ".p12", ".pfx", ".jks", ".keystore", ".tfvars"))
            or "secret" in name or "credential" in name or "service-account" in name or "service_account" in name)


def configuration_type(path):
    name = PurePosixPath(path).name.casefold()
    if is_sensitive_path(path):
        return "Sensitive configuration or secret-like file"
    if name in CONFIG_NAMES:
        return CONFIG_NAMES[name]
    if name.startswith("dockerfile"):
        return "Docker configuration"
    if name.startswith("vite.config."):
        return "Vite configuration"
    if name.startswith("next.config."):
        return "Next.js configuration"
    if name.endswith(".csproj"):
        return ".NET project configuration"
    if name.startswith("appsettings") and name.endswith(".json"):
        return ".NET application settings"
    if name.startswith("settings.") and name.endswith(".py"):
        return "Application settings module"
    if name in {".env.example", ".env.sample", ".env.template", ".env.dist"}:
        return "Environment variable template"
    if path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        return "GitHub Actions workflow"
    if name.endswith((".tf", ".tf.json")):
        return "Terraform configuration"
    if name.endswith((".yaml", ".yml", ".ini", ".conf", ".properties")):
        return "Structured or service configuration"
    if name in {"docker-compose.yml", "docker-compose.yaml"}:
        return "Docker Compose configuration"
    return None


def detect_configurations(records, evidence_for):
    result = []
    for record in records:
        kind = configuration_type(record["path"])
        if not kind:
            continue
        sensitive = is_sensitive_path(record["path"])
        result.append(ConfigurationFile(configuration_type=kind, path=record["path"], sensitive=sensitive,
            content_inspection="intentionally_skipped_sensitive" if sensitive else "metadata_only",
            size_bytes=record["size_bytes"], evidence=evidence_for(record["path"], "configuration_detector")))
    return tuple(result)
