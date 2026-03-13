FROM rust:1.85
WORKDIR /src
COPY edgehub-lora-detector/ ./edgehub-lora-detector/
WORKDIR /src/edgehub-lora-detector
RUN cargo build --release
