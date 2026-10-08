fn main() {
    // Значок вшивается в exe ресурсом (tauri_build), но в список отслеживаемых
    // файлов tauri_build его не вносит: после перерисовки значков
    // (scripts/make_app_icons.py) сборка брала бы прежний ресурс.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    tauri_build::build()
}
