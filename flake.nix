{
  description = "Reproducible native toolchain for the lispmdoc manual decompiler";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      python = pkgs.python312;
      fontsConf = pkgs.writeText "lispmdoc-fonts.conf" ''
        <?xml version="1.0"?>
        <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
        <fontconfig>
          <dir>${pkgs.liberation_ttf}/share/fonts/truetype</dir>
          <cachedir prefix="xdg">fontconfig</cachedir>
          <config><rescan><int>0</int></rescan></config>
        </fontconfig>
      '';
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
          pkgs.pango
          pkgs.cairo
          pkgs.fontconfig
          pkgs.liberation_ttf
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
          export FONTCONFIG_FILE="${fontsConf}"

          mkdir -p "$LISPMDOC_MODEL_ROOT" "$LISPMDOC_CONTAINER_CACHE"
        '';
      };
    };
}
