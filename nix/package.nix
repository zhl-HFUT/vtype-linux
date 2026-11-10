{
  lib,
  stdenv,
  callPackage,
  androidNdk,
  cmake,
  ninja,
  makeWrapper,
  makeDesktopItem,
  copyDesktopItems,
  fcitx5,
  qemu-user,
  python3,
  bash,
  coreutils,
  openssl,
  snappy,
  libopus,
  wxc,
  src,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "vtype-linux";
  version = "0-unstable";
  inherit src;

  strictDeps = true;
  __structuredAttrs = true;
  env.PYTHONDONTWRITEBYTECODE = "1";

  nativeBuildInputs = [
    cmake
    ninja
    makeWrapper
    copyDesktopItems
  ];
  buildInputs = [ fcitx5 ];
  nativeCheckInputs = [
    finalAttrs.passthru.python
    openssl
  ];

  cmakeDir = "../fcitx5-wetype";
  cmakeBuildType = "Release";
  cmakeFlags = [
    "-DWETYPE_PYTHON_EXECUTABLE=${placeholder "out"}/libexec/wetype-python"
  ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export WETYPE_SNAPPY_LIBRARY=${lib.getLib snappy}/lib/libsnappy.so
    ctest --output-on-failure
    (cd .. && ${finalAttrs.passthru.python}/bin/python3 -m unittest discover -s tests -p 'test_*.py')
    (cd .. && ${finalAttrs.passthru.python}/bin/python3 tests/test_snapshot_transport.py)
    runHook postCheck
  '';

  postInstall = ''
    substituteInPlace "$out/share/fcitx5/addon/wetype.conf" \
      --replace-fail 'Library=libfcitx5-wetype' "Library=$out/lib/fcitx5/libfcitx5-wetype"

    mkdir -p "$out/libexec" "$out/bin" "$out/share/vtype-linux/scripts"
    ln -s ${qemu-user}/bin/qemu-aarch64 "$out/libexec/qemu-aarch64-static"
    ln -s ${finalAttrs.passthru.harness}/libexec/wetype-harness "$out/libexec/wetype-harness"
    ln -s ${finalAttrs.passthru.harness}/lib/libandroid.so "$out/libexec/libandroid.so"
    ln -s ${finalAttrs.passthru.harness}/lib/libwetype-cloud.so "$out/libexec/libwetype-cloud.so"
    ln -s ${finalAttrs.passthru.bionic} "$out/libexec/sysroot"
    ln -s ${wxc}/bin/wxc "$out/bin/wxc"

    install -Dm644 ../src/wetype-ime-demo.sh "$out/share/vtype-linux/wetype-ime-demo.sh"
    cp -r ../cloud "$out/share/vtype-linux/cloud"
    find "$out/share/vtype-linux/cloud" -name __pycache__ -prune -exec rm -rf {} +
    find "$out/share/vtype-linux/cloud" -name '*.pyc' -delete
    rm -f "$out/share/vtype-linux/cloud/cloud_net.c"
    install -Dm644 ../scripts/wetype-cloud.py "$out/share/vtype-linux/wetype-cloud.py"
    install -Dm644 ../cloud/实时云桥接.py "$out/libexec/cloud_proxy.py"
    for script in fetch_apk.py 17_disable_scan_sig.py 21_fake_appender.py test_bionic_daemon.py; do
      install -m644 "../scripts/$script" "$out/share/vtype-linux/scripts/$script"
    done
    install -m644 ../nix/setup.py "$out/share/vtype-linux/setup.py"
    install -Dm644 ../assets/wetype-ime.png "$out/share/icons/hicolor/256x256/apps/wetype-ime.png"

    makeWrapper ${finalAttrs.passthru.python}/bin/python3 "$out/libexec/wetype-python" \
      --prefix PATH : ${
        lib.makeBinPath [
          openssl
          wxc
        ]
      } \
      --set-default WETYPE_WXC ${wxc}/bin/wxc \
      --set WETYPE_SNAPPY_LIBRARY ${lib.getLib snappy}/lib/libsnappy.so \
      --set WETYPE_OPUS_LIBRARY ${lib.getLib libopus}/lib/libopus.so
    makeWrapper "$out/libexec/wetype-python" "$out/bin/wetype-cloud" \
      --add-flags "$out/share/vtype-linux/wetype-cloud.py"
    makeWrapper "$out/libexec/wetype-python" "$out/bin/wetype-setup" \
      --add-flags "$out/share/vtype-linux/setup.py --package $out"
    makeWrapper "$out/libexec/wetype-python" "$out/bin/wetype-check" \
      --add-flags "$out/share/vtype-linux/scripts/test_bionic_daemon.py" \
      --add-flags '--engine-dir "''${WETYPE_ENGINE_DIR:-$HOME/.local/lib/wetype-ime/arm64}"' \
      --add-flags "--harness $out/libexec/wetype-harness --qemu $out/libexec/qemu-aarch64-static"
    makeWrapper ${bash}/bin/bash "$out/bin/wetype-demo" \
      --prefix PATH : ${lib.makeBinPath [ coreutils ]} \
      --add-flags "$out/share/vtype-linux/wetype-ime-demo.sh" \
      --run 'export WETYPE_ENGINE_DIR="''${WETYPE_ENGINE_DIR:-$HOME/.local/lib/wetype-ime/arm64}"' \
      --set-default WETYPE_HARNESS ${finalAttrs.passthru.harness}/libexec/wetype-harness \
      --set-default QEMU_AARCH64 ${qemu-user}/bin/qemu-aarch64 \
      --set-default WETYPE_SYSROOT ${finalAttrs.passthru.bionic}
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "wetype-ime";
      desktopName = "VType 初始化";
      comment = "准备微信拼音引擎和词库";
      exec = "wetype-setup";
      icon = "wetype-ime";
      terminal = true;
      categories = [ "Utility" ];
    })
  ];

  doInstallCheck = true;
  nativeInstallCheckInputs = [ finalAttrs.passthru.python ];
  installCheckPhase = ''
    runHook preInstallCheck

    ${finalAttrs.passthru.python}/bin/python3 ../nix/check-wxc.py --package "$out" --wxc ${wxc}/bin/wxc
    ${finalAttrs.passthru.python}/bin/python3 ../nix/check-runtime.py --package "$out"
    find "$out" -name __pycache__ -prune -exec rm -rf {} +
    find "$out" -name '*.pyc' -delete

    runHook postInstallCheck
  '';

  passthru = {
    python = python3.withPackages (ps: [ ps.cryptography ]);
    bionic = callPackage ./bionic.nix { inherit src; };
    harness = callPackage ./harness.nix {
      inherit androidNdk;
      inherit (finalAttrs) version src;
    };
  };

  meta = {
    description = "微信拼音 Fcitx5 插件（引擎和词库需另行下载到用户目录）";
    homepage = "https://github.com/zhl-HFUT/vtype-linux";
    license = lib.licenses.gpl3Only;
    mainProgram = "wetype-setup";
    platforms = [ "x86_64-linux" ];
  };
})
