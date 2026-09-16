use tauri_plugin_shell::ShellExt;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let (mut rx, child) = app
                .shell()
                .sidecar("career-radar-sidecar")?
                .spawn()?;
            // Hold the child for the app lifetime and drain its event channel.
            // Dropping both immediately can terminate or detach the sidecar.
            tauri::async_runtime::spawn(async move {
                let _child = child;
                while rx.recv().await.is_some() {}
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("CareerRadar desktop failed to start");
}
