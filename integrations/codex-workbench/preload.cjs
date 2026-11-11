const { contextBridge, ipcRenderer } = require("electron")
contextBridge.exposeInMainWorld("desktop", {
    chooseFolder: () => ipcRenderer.invoke("choose-folder"),
    chooseDrawing: (save, defaultPath) =>
        ipcRenderer.invoke("choose-drawing", save, defaultPath),
    openExternal: (url) => ipcRenderer.invoke("open-external", url),
})
