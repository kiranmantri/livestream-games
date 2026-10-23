# whisper.cpp HTTP server with the `small` multilingual model baked in (no download at boot, no volume).
# Used by the Fly app `livebingo-stt` (see deploy/stt/fly.toml). Build is slow the first time (compile + 466 MB model).
FROM debian:bookworm-slim AS build
ARG WHISPER_REF=v1.7.6
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 --branch ${WHISPER_REF} https://github.com/ggml-org/whisper.cpp.git .
# static libs: the server binary alone is copied to the runtime image; no GPU, generic x86-64/arm64 build
RUN cmake -B build -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DGGML_NATIVE=OFF \
    && cmake --build build -j"$(nproc)" --config Release --target whisper-server
RUN curl -fL --retry 3 -o /ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin \
    && test "$(stat -c %s /ggml-small.bin)" -gt 400000000

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin stt
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /ggml-small.bin /models/ggml-small.bin
USER stt
EXPOSE 8080
# `::` = IPv6 any (Fly's private network 6PN is IPv6); the app reaches it as http://livebingo-stt.internal:8080
ENTRYPOINT ["whisper-server", "-m", "/models/ggml-small.bin", "--host", "::", "--port", "8080", "-t", "4", "-l", "pt"]
