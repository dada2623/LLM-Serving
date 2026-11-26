FROM scratch
ARG UPSTREAM_REVISION
LABEL org.opencontainers.image.title="EPP-Opt" \
      org.opencontainers.image.source="https://github.com/llm-d/llm-d-router" \
      org.opencontainers.image.revision=$UPSTREAM_REVISION
COPY --chmod=0555 epp /app/epp
COPY --chmod=0555 pd-sidecar /app/pd-sidecar
COPY --chmod=0555 predictor /app/predictor
USER 65532:65532
EXPOSE 9002 9003 9090 8080
ENTRYPOINT ["/app/epp"]
