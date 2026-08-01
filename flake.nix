{
  description = "Reproducible native toolchain for the lispmdoc manual decompiler";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      python = pkgs.python312;
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          python
          pkgs.uv
          pkgs.tesseract
          pkgs.poppler-utils
          pkgs.qpdf
          pkgs.mupdf
          pkgs.resvg
          pkgs.harfbuzz
          pkgs.woff2
          pkgs.potrace
          pkgs.chromium
          pkgs.nodejs_22
          pkgs.jq
          pkgs.curl
          pkgs.git
          pkgs.pkg-config
          pkgs.podman
          python.pkgs.fonttools
        ];

        shellHook = ''
          export PYTHONNOUSERSITE=1
          export UV_PYTHON="${python}/bin/python3.12"
          export LISPMDOC_MODEL_ROOT="''${LISPMDOC_MODEL_ROOT:-$PWD/work/models}"
          export LISPMDOC_CONTAINER_CACHE="''${LISPMDOC_CONTAINER_CACHE:-$PWD/work/containers}"
          export DOCKER_HF_CACHE_PATH="''${DOCKER_HF_CACHE_PATH:-$LISPMDOC_MODEL_ROOT/huggingface}"
          export PATH="$PWD/tools/podman-shims:$PATH"
          export LISPMDOC_CXX_RUNTIME="${pkgs.stdenv.cc.cc.lib}/lib"
          export LISPMDOC_PODMAN_BIN="${pkgs.podman}/bin/podman"
          export CONTAINERS_STORAGE_CONF="/usr/share/containers/storage.conf"

          mkdir -p "$LISPMDOC_MODEL_ROOT" "$LISPMDOC_CONTAINER_CACHE"
        '';
      };
    };
}
