FROM golang:1.27.3-alpine3.24 AS build

WORKDIR /src
ENV CGO_ENABLED=0
COPY go.mod ./
COPY *.go ./
RUN go vet ./... \
    && go test ./... \
    && go build -trimpath -ldflags="-s -w" -o /out/gh-concept .

FROM scratch AS runtime

COPY --from=build /out/gh-concept /gh-concept
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/gh-concept"]
