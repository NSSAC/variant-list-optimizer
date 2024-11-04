FROM ghcr.io/nssac/mambascif

ARG VERSION
ENV VERSION=${VERSION:-0.0.0}

RUN mkdir -p /run/secrets

RUN --mount=type=secret,id=gh_token --mount=type=bind,target=/docker_context\
    curl https://$(cat /run/secrets/gh_token)@raw.githubusercontent.com/NSSAC/SciducTainer/refs/heads/main/sciduct.scif > /tmp/sciduct.scif &&\
    scif install /tmp/sciduct.scif

RUN --mount=type=secret,id=gh_token --mount=type=bind,target=/docker_context\
    scif install /docker_context/vlopt.scif

RUN rm -rf /run/secrets || true

ENTRYPOINT ["scif"]

CMD ["shell"]