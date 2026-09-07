FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV KOBOBRIDGE_PORT=8484
ENV XDG_DATA_HOME=/data
VOLUME ["/data"]
EXPOSE 8484

RUN useradd --create-home --uid 10001 bridge && mkdir -p /data && chown bridge /data
USER bridge

ENTRYPOINT ["kobobridge"]
CMD ["run", "--host", "0.0.0.0", "--port", "8484"]
