const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require("electron")
const path = require("node:path")
const fs = require("node:fs")
const { spawn } = require("node:child_process")
let child
let mainWindow
const stateDir = path.join(__dirname, "state")
const port = Number(process.env.CODEX_WORKBENCH_PORT || 61429)

async function backendHealth() {
    try {
        return await fetch(`http://127.0.0.1:${port}/health`, {
            signal: AbortSignal.timeout(1200),
        }).then((r) => r.json())
    } catch {
        return null
    }
}

async function ensureBackend() {
    let current = await backendHealth()
    if (current && current.service !== "drawio-codex-workbench")
        throw new Error("工作台端口已被其他服务使用。")
    if (
        current?.projectRoot &&
        path.resolve(current.projectRoot).toLowerCase() !==
            path.resolve(__dirname).toLowerCase()
    )
        throw new Error(
            "另一个项目的工作台正在运行，请先结束那个项目的开发服务。",
        )
    if (current && current.apiVersion !== 2) {
        const token = fs
            .readFileSync(path.join(stateDir, "token.txt"), "utf8")
            .trim()
        const response = await fetch(`http://127.0.0.1:${port}/api/shutdown`, {
            method: "POST",
            headers: {
                Authorization: "Bearer " + token,
                "Content-Type": "application/json",
            },
            body: "{}",
        })
        if (!response.ok)
            throw new Error(
                (await response.json()).error ||
                    "请等待后台任务完成后重新打开工作台。",
            )
        for (let n = 0; n < 40; n++) {
            await new Promise((r) => setTimeout(r, 150))
            if (!(await backendHealth())) break
        }
        current = null
    }
    let startError
    if (!current) {
        const python = process.env.DRAWIO_PYTHON || "python"
        const log = fs.openSync(path.join(stateDir, "server.log"), "a")
        child = spawn(python, [path.join(__dirname, "server.py")], {
            cwd: __dirname,
            windowsHide: true,
            stdio: ["ignore", log, log],
        })
        child.once("error", (error) => {
            startError = error
        })
        fs.closeSync(log)
    }
    for (let n = 0; n < 150; n++) {
        if (startError)
            throw new Error("Python 服务无法启动：" + startError.message)
        const health = await backendHealth()
        if (
            health?.service === "drawio-codex-workbench" &&
            health.ready &&
            health.apiVersion === 2
        )
            return
        await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error("本地服务尚未就绪，请查看项目 state/server.log。")
}

async function start() {
    fs.mkdirSync(stateDir, { recursive: true })
    await ensureBackend()
    const token = fs
        .readFileSync(path.join(stateDir, "token.txt"), "utf8")
        .trim()
    const win = new BrowserWindow({
        width: 1540,
        height: 960,
        minWidth: 1000,
        minHeight: 650,
        backgroundColor: "#111417",
        title: "Drawio · Codex 工作台",
        webPreferences: {
            preload: path.join(__dirname, "preload.cjs"),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
        },
    })
    mainWindow = win
    Menu.setApplicationMenu(
        Menu.buildFromTemplate([
            {
                label: "查看",
                submenu: [
                    {
                        label: "重新载入工作台",
                        accelerator: "CommandOrControl+R",
                        click: () => win.webContents.reloadIgnoringCache(),
                    },
                ],
            },
        ]),
    )
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }))
    win.webContents.on("will-navigate", (event, url) => {
        if (!url.startsWith(`http://127.0.0.1:${port}/`)) event.preventDefault()
    })
    await win.loadURL(`http://127.0.0.1:${port}/#${token}`)
    win.setMenuBarVisibility(false)
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
}
ipcMain.handle("choose-folder", async (event) => {
    const result = await dialog.showOpenDialog(
        BrowserWindow.fromWebContents(event.sender),
        { title: "选择项目文件夹", properties: ["openDirectory"] },
    )
    return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle("open-external", async (_event, value) => {
    const url = new URL(value)
    if (
        url.protocol !== "https:" &&
        !(
            url.protocol === "http:" &&
            ["localhost", "127.0.0.1"].includes(url.hostname)
        )
    )
        throw new Error("仅支持安全网页地址。")
    return shell.openExternal(url.href)
})
ipcMain.handle("choose-drawing", async (event, save, defaultPath) => {
    const parent = BrowserWindow.fromWebContents(event.sender)
    const options = {
        title: save ? "保存图纸" : "打开图纸",
        defaultPath,
        filters: [{ name: "draw.io 图纸", extensions: ["drawio", "xml"] }],
    }
    const result = save
        ? await dialog.showSaveDialog(parent, options)
        : await dialog.showOpenDialog(parent, {
              ...options,
              properties: ["openFile"],
          })
    return result.canceled ? null : save ? result.filePath : result.filePaths[0]
})
if (!app.requestSingleInstanceLock()) app.quit()
else {
    app.on("second-instance", () => {
        if (mainWindow) {
            if (mainWindow.isMinimized()) mainWindow.restore()
            mainWindow.show()
            mainWindow.focus()
        }
    })
    app.whenReady()
        .then(start)
        .catch((e) => {
            dialog.showErrorBox("工作台启动失败", String(e))
            app.quit()
        })
}
app.on("window-all-closed", () => app.quit())
// The local backend remains available so a running conversation can finish.
