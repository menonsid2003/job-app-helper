FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# python-jobspy itself, separate from requirements.txt (which only lists its
# runtime deps) — its own published metadata hard-pins NUMPY==1.26.3, a
# version with no wheel for the Python version in this image, so a plain
# `pip install` of it tries to compile numpy from source and fails without a
# C toolchain. --no-deps skips that broken pin; requirements.txt above
# already covers everything it actually imports at runtime. Needed even
# though jobspy.enabled defaults to false in criteria.example.yaml — the
# JobSpy connector otherwise throws ModuleNotFoundError the moment someone
# turns it on from Settings, silently producing zero discovered jobs.
RUN pip install --no-cache-dir --no-deps "python-jobspy>=1.1.80"
# Chromium binary + its OS-level deps for Playwright (the adapter-based
# auto-apply path). Off by default (auto_apply_enabled: false), but without
# this, turning it on from Settings fails the same silent way jobspy did
# above — the package is installed via requirements.txt, but Playwright
# still needs its own browser binary downloaded separately.
RUN playwright install --with-deps chromium

COPY app ./app
COPY criteria.yaml .

# resume/ and data/ are meant to be bind-mounted in docker-compose.yml so the
# user's resume and the SQLite DB persist outside the container.
RUN mkdir -p resume data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
