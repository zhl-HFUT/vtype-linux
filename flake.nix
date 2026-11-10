{
  description = "VType Linux：微信拼音 Fcitx5 插件";

  inputs.nixpkgs.url = "https://channels.nixos.org/nixos-unstable/nixexprs.tar.zst";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      wxc = pkgs.callPackage ./nix/wxc.nix { };
      package = pkgs.callPackage ./nix/package.nix {
        inherit wxc;
        androidNdk = pkgs.pkgsCross.aarch64-android.buildPackages.androidndkPkgs_29;
        src = pkgs.lib.cleanSourceWith {
          src = self;
          filter =
            path: type:
            pkgs.lib.cleanSourceFilter path type
            && baseNameOf path != ".jj"
            && baseNameOf path != "__pycache__";
        };
      };
    in
    {
      packages.${system} = {
        inherit wxc;
        default = package;
        vtype-linux = package;
      };
      apps.${system} = {
        wxc = {
          type = "app";
          program = "${wxc}/bin/wxc";
          meta.description = "检查微信输入法设备关联客户端";
        };
        wetype-demo = {
          type = "app";
          program = "${package}/bin/wetype-demo";
          meta.description = "在终端中试打微信拼音";
        };
        wetype-cloud = {
          type = "app";
          program = "${package}/bin/wetype-cloud";
          meta.description = "管理云候选、词库同步和剪贴板互通";
        };
        wetype-setup = {
          type = "app";
          program = "${package}/bin/wetype-setup";
          meta.description = "下载并安装微信拼音引擎资源";
        };
        wetype-check = {
          type = "app";
          program = "${package}/bin/wetype-check";
          meta.description = "自检已安装的微信拼音引擎";
        };
      };
      checks.${system}.default = package;
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          cmake
          ninja
          fcitx5
          pkg-config
          openssl
          snappy
          libopus
          qemu-user
          e2fsprogs
          unzip
          curl
          file
          xorg-server
          xclip
          wl-clipboard
          xdotool
          xwininfo
          gtk3
          wxc
          (python3.withPackages (ps: [
            ps.cryptography
            ps.dbus-python
            ps.pygobject3
          ]))
        ];
        WETYPE_SNAPPY_LIBRARY = "${pkgs.lib.getLib pkgs.snappy}/lib/libsnappy${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
        WETYPE_OPUS_LIBRARY = "${pkgs.lib.getLib pkgs.libopus}/lib/libopus${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
      };
      formatter.${system} = pkgs.nixfmt;
    };
}
