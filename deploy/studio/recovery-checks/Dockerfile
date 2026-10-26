# Explicit disposable Linux acceptance, not a production or desktop image.
# Build context needs only the package, selected tests, fixtures and examples.
ARG PYTHON_IMAGE=python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /checks
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY nexus ./nexus
RUN pip install --no-cache-dir . "pytest>=9,<10" "pytest-asyncio>=1,<2" \
    "sqlalchemy>=2,<3" "pypdf>=6,<7"
COPY tests ./tests
COPY examples/nexus-bot/src ./examples/nexus-bot/src
RUN python tests/conftest.py
USER 65534:65534
ENTRYPOINT ["python", "-m", "pytest", "-o", "addopts=", "-q"]
CMD ["tests/test_studio_termination.py", "tests/studio_server_recovery_acceptance.py", "tests/test_studio_backup.py", "tests/test_studio_sql_restore.py", "tests/test_studio_desktop.py", "tests/test_studio_desktop_parent.py", "tests/studio_desktop_parent_acceptance.py"]
