const $ = (id) => document.getElementById(id)
const token = location.hash.slice(1) || sessionStorage.getItem("connection")
sessionStorage.setItem("connection", token || "")
history.replaceState(null, "", location.pathname)
let historyLoaded = false
let threadPagesLoaded = 0
const draftCache = new Map()
let renderedOlderCount = 0
let latestSnapshot = null,
    eventController = null,
    lastEventState = null,
    approvalSignature = ""
let settings,
    threads = [],
    nextThreads = null,
    selected = null,
    canvas = null,
    frameReady = false,
    loadingCanvas = false,
    dirty = false,
    pendingXml = null,
    saveTimer,
    polling = false,
    epoch = 0,
    historyCursor = null,
    olderTurns = [],
    lastSnapshot = "",
    canvasConflict = false
const frame = $("editor")
async function api(path, body) {
    const r = await fetch("/api/" + path, {
        method: body ? "POST" : "GET",
        headers: {
            Authorization: "Bearer " + token,
            ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
    })
    const data = await r.json()
    if (!r.ok) {
        const error = new Error(data.error || "请求失败")
        error.status = r.status
        throw error
    }
    return data
}
function toast(message) {
    $("toast").textContent = message
    $("toast").hidden = false
    clearTimeout(toast.timer)
    toast.timer = setTimeout(() => ($("toast").hidden = true), 6500)
}
function shortPath(path) {
    return (path || "本地项目")
        .replace(/[\\/]+$/, "")
        .split(/[\\/]/)
        .pop()
}
function displayText(text) {
    return String(text || "")
        .split("<drawio_workbench_context>")[0]
        .trim()
}
function setProject(path) {
    settings.project = path
    $("project-name").textContent = shortPath(path)
    $("project").title = path
}
function title(thread) {
    return (
        (thread.name && thread.name !== "新绘图对话"
            ? thread.name
            : displayText(thread.preview)) ||
        thread.name ||
        "新对话"
    )
}
function drawingLabel(path) {
    const name = shortPath(path)
    return /^[0-9a-f-]{36}\.drawio$/i.test(name) ? "对话图纸.drawio" : name
}
function threadStamp(thread) {
    const value = Number(thread.recencyAt ?? thread.updatedAt ?? 0)
    return value < 1e12 ? value * 1000 : value
}
function sortedThreads() {
    return [...threads].sort((a, b) => threadStamp(b) - threadStamp(a))
}
function saveDraft(id) {
    if (!id) return
    const value = $("prompt").value
    draftCache.set(id, value)
    try {
        if (value) localStorage.setItem("codex-draft:" + id, value)
        else localStorage.removeItem("codex-draft:" + id)
    } catch {}
}
function restoreDraft(id) {
    let value = draftCache.get(id)
    if (value === undefined) {
        try {
            value = localStorage.getItem("codex-draft:" + id) || ""
        } catch {
            value = ""
        }
    }
    $("prompt").value = value
}
function setRecentsOpen(open) {
    document.body.classList.toggle("recents-collapsed", !open)
    $("recents-nav").hidden = !open
    $("toggle-recents").setAttribute("aria-expanded", String(open))
    try {
        localStorage.setItem("recents-open", String(open))
    } catch {}
}
function chooseThread(id) {
    return selectThread(id).catch((error) => toast(error.message))
}
function renderRecents() {
    const list = $("recent-thread-list"),
        previousScroll = list.scrollTop
    const needle = $("recent-search").value.trim().toLowerCase()
    const scope = $("recent-scope").value
    const matches = sortedThreads().filter(
        (thread) =>
            (scope === "all" || thread.cwd === settings?.project) &&
            (title(thread) + " " + (thread.cwd || ""))
                .toLowerCase()
                .includes(needle),
    )
    const signature = JSON.stringify(
        matches.map((thread) => [
            thread.id,
            title(thread),
            thread.cwd,
            threadStamp(thread),
        ]),
    )
    if (list.dataset.signature !== signature) {
        list.dataset.signature = signature
        list.replaceChildren()
        for (const thread of matches) {
            const row = document.createElement("button")
            row.className = "recent-row"
            row.dataset.threadId = thread.id
            row.title = title(thread) + "\n" + (thread.cwd || "")
            const name = document.createElement("span")
            name.className = "recent-title"
            name.textContent = title(thread)
            const meta = document.createElement("span")
            meta.className = "recent-meta"
            const folder = document.createElement("span")
            folder.textContent = shortPath(thread.cwd)
            const date = document.createElement("span")
            const stamp = threadStamp(thread)
            date.textContent = stamp
                ? new Date(stamp).toLocaleDateString("zh-CN", {
                      month: "numeric",
                      day: "numeric",
                  })
                : ""
            meta.append(folder, date)
            row.append(name, meta)
            row.onclick = () => chooseThread(thread.id)
            list.append(row)
        }
        if (!matches.length) {
            const empty = document.createElement("p")
            empty.className = "recent-empty"
            empty.textContent = needle ? "没有匹配的最近对话" : "这里还没有对话"
            list.append(empty)
        }
        list.scrollTop = previousScroll
    }
    for (const row of list.querySelectorAll(".recent-row")) {
        const active = row.dataset.threadId === selected
        row.classList.toggle("selected", active)
        if (active) row.setAttribute("aria-current", "true")
        else row.removeAttribute("aria-current")
    }
    $("recent-load-more").hidden = !nextThreads
}
async function refreshThreads(append = false) {
    const result = await api(
        "threads" +
            (append && nextThreads
                ? "?cursor=" + encodeURIComponent(nextThreads)
                : ""),
    )
    if (append) {
        threads = [...threads, ...result.data]
        threadPagesLoaded++
    } else
        threads = threadPagesLoaded ? [...result.data, ...threads] : result.data
    threads = [
        ...new Map(threads.map((thread) => [thread.id, thread])).values(),
    ]
    if (append || !threadPagesLoaded) nextThreads = result.nextCursor
    renderPicker()
    renderRecents()
}
function renderPicker() {
    const needle = $("search").value.toLowerCase()
    const all = $("all-projects").checked
    const matches = sortedThreads().filter(
        (t) =>
            (all || t.cwd === settings.project) &&
            (title(t) + " " + t.cwd).toLowerCase().includes(needle),
    )
    $("thread-list").replaceChildren()
    for (const thread of matches) {
        const row = document.createElement("button")
        row.className =
            "thread-row" + (thread.id === selected ? " selected" : "")
        const name = document.createElement("div")
        name.className = "thread-title"
        name.textContent = title(thread)
        const sub = document.createElement("div")
        sub.className = "thread-sub"
        const folder = document.createElement("span")
        folder.textContent = shortPath(thread.cwd)
        folder.title = thread.cwd || ""
        const date = document.createElement("span")
        let stamp = Number(thread.updatedAt || 0)
        if (stamp < 1e12) stamp *= 1000
        date.textContent = new Date(stamp).toLocaleDateString("zh-CN")
        sub.append(folder, date)
        row.append(name, sub)
        row.onclick = () => chooseThread(thread.id)
        $("thread-list").append(row)
    }
    $("load-more-threads").hidden = !nextThreads
    if (!matches.length) {
        const none = document.createElement("p")
        none.className = "muted"
        none.style.padding = "14px"
        none.textContent = "没有匹配的对话"
        $("thread-list").append(none)
    }
}
function loadCanvas(value) {
    canvas = value
    pendingXml = null
    dirty = false
    canvasConflict = false
    $("conflict").hidden = true
    $("drawing-name").textContent = drawingLabel(value.path)
    $("drawing-name").title = value.path
    $("save-state").textContent = "已保存"
    if (frameReady) {
        loadingCanvas = true
        frame.contentWindow.postMessage(
            JSON.stringify({
                action: "load",
                xml: value.xml,
                autosave: 1,
                title: shortPath(value.path),
            }),
            location.origin,
        )
        setTimeout(() => (loadingCanvas = false), 550)
    }
}
async function selectThread(id) {
    if (selected === id && latestSnapshot) {
        $("picker").hidden = true
        renderRecents()
        return
    }
    if (dirty) {
        try {
            await saveCanvas()
        } catch {
            return
        }
    }
    const previous = selected,
        previousTitle = $("conversation-name").textContent
    saveDraft(previous)
    const myEpoch = ++epoch
    eventController?.abort()
    selected = null
    latestSnapshot = null
    lastEventState = null
    approvalSignature = ""
    $("approvals").replaceChildren()
    $("stop").hidden = true
    $("prompt").disabled = true
    olderTurns = []
    renderedOlderCount = 0
    historyCursor = null
    historyLoaded = false
    lastSnapshot = ""
    $("picker").hidden = true
    $("conversation-name").textContent = "正在载入…"
    try {
        const result = await api("select", { threadId: id })
        if (myEpoch !== epoch) return
        selected = id
        setProject(result.thread.cwd || settings.project)
        $("conversation-name").textContent = title(result.thread)
        restoreDraft(id)
        loadCanvas(result.canvas)
        renderRecents()
        $("messages").replaceChildren()
        await poll()
        startEvents(id, myEpoch)
    } catch (error) {
        if (myEpoch === epoch) {
            selected = previous
            $("conversation-name").textContent = previousTitle
            restoreDraft(previous)
            renderRecents()
            if (previous) {
                await poll()
                startEvents(previous, myEpoch)
            }
        }
        throw error
    } finally {
        if (myEpoch === epoch) $("prompt").disabled = false
    }
}
async function newChat() {
    try {
        const result = await api("new", { cwd: settings.project })
        await refreshThreads()
        await selectThread(result.thread.id)
        $("prompt").focus()
    } catch (e) {
        toast(e.message)
    }
}
async function saveCanvas(path) {
    if (!selected || !canvas || (!pendingXml && !path)) return
    const sid = selected,
        xml = pendingXml || canvas.xml
    try {
        const result = await api("canvas", {
            threadId: sid,
            xml,
            version: canvas.version,
            ...(path ? { path } : {}),
        })
        if (selected === sid) {
            canvas = result
            if (pendingXml === xml) pendingXml = null
            dirty = !!pendingXml
            $("save-state").textContent = dirty ? "正在保存…" : "已保存"
            $("drawing-name").textContent = drawingLabel(canvas.path)
            $("drawing-name").title = canvas.path
        }
        return result
    } catch (e) {
        if (e.status === 409) {
            canvasConflict = true
            $("conflict").hidden = false
            $("save-state").textContent = "修改冲突"
        } else toast(e.message)
        throw e
    }
}
window.addEventListener("message", (event) => {
    if (
        event.source !== frame.contentWindow ||
        event.origin !== location.origin
    )
        return
    let message
    try {
        message = JSON.parse(event.data)
    } catch {
        return
    }
    if (message.event === "configure")
        frame.contentWindow.postMessage(
            JSON.stringify({
                action: "configure",
                config: {
                    defaultFonts: ["Microsoft YaHei", "Arial"],
                    enableAi: false,
                },
            }),
            location.origin,
        )
    if (message.event === "init") {
        frameReady = true
        if (canvas) loadCanvas(canvas)
        else
            frame.contentWindow.postMessage(
                JSON.stringify({
                    action: "load",
                    xml: '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel>',
                    autosave: 1,
                }),
                location.origin,
            )
    }
    if (
        (message.event === "autosave" || message.event === "save") &&
        selected &&
        message.xml &&
        !loadingCanvas
    ) {
        pendingXml = message.xml
        dirty = true
        $("save-state").textContent = "正在保存…"
        clearTimeout(saveTimer)
        saveTimer = setTimeout(() => saveCanvas().catch(() => {}), 400)
    }
})
function addMessage(role, text, live = false) {
    if (!text) return
    const box = document.createElement("article")
    box.className = "message " + role + (live ? " live" : "")
    const label = document.createElement("div")
    label.className = "role"
    label.textContent = role === "user" ? "你" : "Codex"
    const body = document.createElement("div")
    body.className = "body"
    body.textContent = displayText(text)
    box.append(label, body)
    $("messages").append(box)
}
function toolItem(item) {
    const labels = {
        commandExecution: "运行命令",
        fileChange: "修改文件",
        mcpToolCall: "调用工具",
        dynamicToolCall: "调用工具",
        webSearch: "检索资料",
        contextCompaction: "整理上下文",
    }
    if (!labels[item.type]) return
    const box = document.createElement("details")
    box.className = "tool"
    const summary = document.createElement("summary")
    summary.textContent =
        labels[item.type] +
        (item.status
            ? " · " +
              ({ completed: "已完成", inProgress: "进行中", failed: "失败" }[
                  item.status
              ] || item.status)
            : "")
    const pre = document.createElement("pre")
    pre.textContent = String(
        item.command ||
            item.aggregatedOutput ||
            JSON.stringify(item.changes || item.arguments || {}, null, 2),
    ).slice(0, 20000)
    box.append(summary, pre)
    $("messages").append(box)
}
function renderApprovals(approvals) {
    const signature = JSON.stringify(approvals)
    if (signature === approvalSignature) return
    approvalSignature = signature
    $("approvals").replaceChildren()
    for (const p of approvals) {
        const box = document.createElement("div")
        box.className = "approval"
        box.dataset.requestId = String(p.requestId)
        const title = document.createElement("strong")
        title.textContent = p.method.includes("requestUserInput")
            ? "Codex 需要你的回答"
            : p.method.includes("permissions/")
              ? "请求额外权限"
              : p.method.includes("elicitation")
                ? "工具需要补充信息"
                : "Codex 请求执行操作"
        const details = document.createElement("pre")
        details.textContent =
            p.params.command ||
            p.params.reason ||
            p.params.message ||
            JSON.stringify(
                p.params.permissions || p.params.changes || {},
                null,
                2,
            )
        box.append(title, details)
        const submit = async (body) => {
            try {
                box.querySelectorAll("button").forEach((b) => {
                    b.disabled = true
                })
                await api("approval", {
                    threadId: p.threadId,
                    requestId: p.requestId,
                    ...body,
                })
                await poll()
            } catch (e) {
                toast(e.message)
                box.querySelectorAll("button").forEach((b) => {
                    b.disabled = false
                })
            }
        }
        const button = (text, body) => {
            const b = document.createElement("button")
            b.textContent = text
            b.onclick = () => submit(typeof body === "function" ? body() : body)
            box.append(b)
        }
        if (p.method.includes("requestUserInput")) {
            const fields = []
            for (const q of p.params.questions || []) {
                const label = document.createElement("label")
                label.textContent = q.question
                const input = document.createElement("input")
                input.type = q.isSecret ? "password" : "text"
                input.className = "approval-input"
                input.setAttribute("aria-label", q.question)
                if (q.options?.length) {
                    const options = document.createElement("datalist")
                    options.id = "options-" + p.requestId + "-" + q.id
                    for (const option of q.options) {
                        const choice = document.createElement("option")
                        choice.value = option.label
                        choice.textContent = option.description || ""
                        options.append(choice)
                    }
                    input.setAttribute("list", options.id)
                    label.append(options)
                }
                label.append(input)
                box.append(label)
                fields.push([q.id, input])
            }
            button("提交回答", () => ({
                answers: Object.fromEntries(
                    fields.map(([id, input]) => [
                        id,
                        { answers: [input.value] },
                    ]),
                ),
            }))
        } else if (p.method === "mcpServer/elicitation/request") {
            const fields = []
            if (p.params.mode === "url") {
                const open = document.createElement("button")
                open.textContent = "在浏览器中完成"
                open.onclick = () => window.desktop?.openExternal(p.params.url)
                box.append(open)
            } else {
                const schema = p.params.requestedSchema || {}
                for (const [key, spec] of Object.entries(
                    schema.properties || {},
                )) {
                    const label = document.createElement("label")
                    label.textContent = spec.title || key
                    const input = spec.enum
                        ? document.createElement("select")
                        : document.createElement("input")
                    if (spec.enum) {
                        for (const value of spec.enum) {
                            const option = document.createElement("option")
                            option.value = String(value)
                            option.textContent = String(value)
                            input.append(option)
                        }
                    } else
                        input.type =
                            spec.type === "boolean"
                                ? "checkbox"
                                : ["integer", "number"].includes(spec.type)
                                  ? "number"
                                  : "text"
                    input.className = "approval-input"
                    if (spec.default !== undefined) {
                        if (input.type === "checkbox")
                            input.checked = spec.default
                        else input.value = String(spec.default)
                    }
                    label.append(input)
                    box.append(label)
                    fields.push([key, spec, input])
                }
            }
            button(p.params.mode === "url" ? "我已完成" : "提交", () => ({
                accept: true,
                content:
                    p.params.mode === "url"
                        ? null
                        : Object.fromEntries(
                              fields.map(([key, spec, input]) => [
                                  key,
                                  spec.type === "boolean"
                                      ? input.checked
                                      : ["integer", "number"].includes(
                                              spec.type,
                                          )
                                        ? Number(input.value)
                                        : input.value,
                              ]),
                          ),
            }))
            button("拒绝", { accept: false })
        } else {
            button("允许这次", { accept: true })
            button("拒绝", { accept: false })
        }
        $("approvals").append(box)
    }
}

async function startEvents(id, myEpoch) {
    eventController?.abort()
    const controller = new AbortController()
    eventController = controller
    while (!controller.signal.aborted && myEpoch === epoch) {
        try {
            const response = await fetch(
                "/api/events?threadId=" + encodeURIComponent(id),
                {
                    headers: { Authorization: "Bearer " + token },
                    signal: controller.signal,
                },
            )
            if (!response.ok) throw new Error("实时连接暂不可用")
            const reader = response.body.getReader(),
                decoder = new TextDecoder()
            let buffer = ""
            while (true) {
                const { value, done } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })
                let index
                while ((index = buffer.indexOf("\n\n")) >= 0) {
                    const block = buffer.slice(0, index)
                    buffer = buffer.slice(index + 2)
                    const line = block
                        .split("\n")
                        .find((s) => s.startsWith("data: "))
                    if (!line || myEpoch !== epoch) continue
                    const state = JSON.parse(line.slice(6))
                    if (state.threadId !== selected) continue
                    lastEventState = state
                    if (latestSnapshot)
                        renderSnapshot({ ...latestSnapshot, ...state })
                }
            }
        } catch (error) {
            if (error.name === "AbortError") return
        }
        await new Promise((resolve) => setTimeout(resolve, 1000))
    }
}

function renderSnapshot(data) {
    if (lastEventState && lastEventState.revision > (data.revision || 0))
        data = { ...data, ...lastEventState }
    latestSnapshot = data
    const key = JSON.stringify({
        data: data.data,
        queue: data.queue,
        live: data.live,
        olderTurns,
        historyCursor,
        historyLoaded,
    })
    if (key !== lastSnapshot) {
        const oldScroll = $("messages").scrollTop,
            oldHeight = $("messages").scrollHeight,
            wasEmpty = !$("messages").childElementCount
        const addedOlder = olderTurns.length > renderedOlderCount
        renderedOlderCount = olderTurns.length
        const nearBottom =
            $("messages").scrollHeight -
                $("messages").scrollTop -
                $("messages").clientHeight <
            110
        lastSnapshot = key
        $("messages").replaceChildren()
        if (historyLoaded ? historyCursor : data.nextCursor) {
            const load = document.createElement("button")
            load.className = "load-history"
            load.textContent = "载入更早的消息"
            load.onclick = async () => {
                const pageThread = selected,
                    pageEpoch = epoch
                const response = await api(
                    "snapshot?threadId=" +
                        encodeURIComponent(pageThread) +
                        "&cursor=" +
                        encodeURIComponent(historyCursor),
                )
                if (selected !== pageThread || epoch !== pageEpoch) return
                olderTurns = [...olderTurns, ...response.data]
                historyCursor = response.nextCursor
                historyLoaded = true
                renderSnapshot({ ...data, nextCursor: historyCursor })
            }
            $("messages").append(load)
        }
        const seen = new Set()
        const turns = [...olderTurns, ...data.data].sort(
            (a, b) => (a.startedAt || 0) - (b.startedAt || 0),
        )
        for (const turn of turns) {
            for (const item of turn.items || []) {
                if (seen.has(item.id)) continue
                seen.add(item.id)
                if (item.type === "userMessage")
                    addMessage(
                        "user",
                        (item.content || [])
                            .filter((x) => x.type === "text")
                            .map((x) => x.text)
                            .join("\n"),
                    )
                else if (item.type === "agentMessage")
                    addMessage(
                        "assistant",
                        data.live?.[item.id] || item.text,
                        !!data.live?.[item.id] && data.job === "running",
                    )
                else toolItem(item)
            }
            if (turn.status === "interrupted")
                addMessage("assistant", "本轮回复已停止。")
            if (turn.status === "failed")
                addMessage(
                    "assistant",
                    "执行未完成：" + (turn.error?.message || "请重试。"),
                )
        }
        for (const [id, text] of Object.entries(data.live || {})) {
            if (!seen.has(id)) addMessage("assistant", text, true)
        }
        for (const queued of data.queue || []) {
            const div = document.createElement("div")
            div.className = "queue-message"
            div.textContent =
                "等待处理 · " +
                displayText(
                    (queued.input || [])
                        .filter((x) => x.type === "text")
                        .map((x) => x.text)
                        .join("\n"),
                )
            $("messages").append(div)
        }
        if (!turns.length && !data.queue?.length)
            addMessage(
                "assistant",
                "这是一段新的 Codex 对话。你可以开始提问，也可以在左侧编辑图纸。",
            )
        if (nearBottom || wasEmpty)
            $("messages").scrollTop = $("messages").scrollHeight
        else
            $("messages").scrollTop =
                oldScroll +
                (addedOlder ? $("messages").scrollHeight - oldHeight : 0)
    }
    const working =
        data.data.some((t) => t.status === "inProgress") ||
        data.queue.length ||
        ["running", "starting"].includes(data.job)
    $("chat-status").textContent = data.jobError
        ? "执行遇到问题"
        : working
          ? "Codex 正在处理…"
          : "与本机 Codex 共享会话"
    $("sync-state").textContent =
        data.job === "running"
            ? "实时显示"
            : data.job === "desktop"
              ? "Desktop · 历史同步"
              : "已同步"
    $("stop").hidden = !data.canInterrupt
    if (data.jobError) $("chat-status").title = data.jobError
    renderApprovals(data.approvals || [])
}
async function poll() {
    if (!selected || polling) return
    polling = true
    const id = selected,
        myEpoch = epoch
    try {
        const [data, drawing] = await Promise.all([
            api("snapshot?threadId=" + encodeURIComponent(id)),
            api("canvas?threadId=" + encodeURIComponent(id)),
        ])
        if (myEpoch !== epoch) return
        if (!historyLoaded) historyCursor = data.nextCursor
        renderSnapshot(data)
        if (canvas && drawing.version !== canvas.version) {
            if (dirty) {
                canvasConflict = true
                $("conflict").hidden = false
            } else loadCanvas(drawing)
        }
        $("connection").textContent = "本机已连接"
    } catch (e) {
        $("connection").textContent = "连接重试中"
        $("chat-status").textContent = e.message
    } finally {
        polling = false
    }
}
$("toggle-recents").onclick = () => setRecentsOpen($("recents-nav").hidden)
$("collapse-recents").onclick = () => setRecentsOpen(false)
$("recent-new-chat").onclick = newChat
$("recent-search").oninput = renderRecents
$("recent-scope").onchange = () => {
    try {
        localStorage.setItem("recent-scope", $("recent-scope").value)
    } catch {}
    renderRecents()
}
$("recent-refresh").onclick = () =>
    refreshThreads().catch((error) => toast(error.message))
$("recent-load-more").onclick = () =>
    refreshThreads(true).catch((error) => toast(error.message))
$("prompt").addEventListener("input", () => saveDraft(selected))
window.addEventListener("beforeunload", () => saveDraft(selected))
$("conversation").onclick = () => {
    $("picker").hidden = !$("picker").hidden
    if (!$("picker").hidden) {
        refreshThreads().catch((e) => toast(e.message))
        $("search").focus()
    }
}
$("search").oninput = renderPicker
$("all-projects").onchange = renderPicker
$("load-more-threads").onclick = () =>
    refreshThreads(true).catch((e) => toast(e.message))
$("new-chat").onclick = newChat
$("refresh").onclick = async () => {
    await refreshThreads()
    await poll()
}
$("project").onclick = async () => {
    const path = await window.desktop?.chooseFolder()
    if (!path) return
    await api("project", { cwd: path })
    setProject(path)
    await refreshThreads()
    const match = threads.find((t) => t.cwd === path)
    if (match) await selectThread(match.id)
    else await newChat()
}
$("open-file").onclick = async () => {
    if (!selected) {
        toast("请先选择或新建对话。")
        return
    }
    const path = await window.desktop?.chooseDrawing(false, settings.project)
    if (path) loadCanvas(await api("open", { threadId: selected, path }))
}
$("save-file").onclick = async () => {
    if (!canvas) {
        toast("请先选择对话。")
        return
    }
    const path = await window.desktop?.chooseDrawing(true, canvas.path)
    if (path) await saveCanvas(path)
}
$("reload-canvas").onclick = async () =>
    loadCanvas(await api("canvas?threadId=" + encodeURIComponent(selected)))
$("keep-copy").onclick = async () => {
    const path = await window.desktop?.chooseDrawing(
        true,
        canvas.path.replace(/\.drawio$/, "-副本.drawio"),
    )
    if (path) {
        const current = await api(
            "canvas?threadId=" + encodeURIComponent(selected),
        )
        canvas.version = current.version
        await saveCanvas(path)
        canvasConflict = false
        $("conflict").hidden = true
    }
}
$("stop").onclick = async () => {
    const id = selected
    $("stop").disabled = true
    try {
        await api("interrupt", { threadId: id })
        await poll()
    } catch (e) {
        toast(e.message)
    } finally {
        $("stop").disabled = false
    }
}
$("composer").onsubmit = async (event) => {
    event.preventDefault()
    if (!selected) {
        toast("请先选择或新建对话。")
        return
    }
    const text = $("prompt").value.trim()
    if (!text) return
    const targetThread = selected,
        sendEpoch = epoch,
        attachDrawing = $("attach").checked
    saveDraft(targetThread)
    if (canvasConflict) {
        toast("请先处理图纸的外部修改。")
        return
    }
    $("send").disabled = true
    try {
        if (dirty) await saveCanvas()
        await api("send", {
            threadId: targetThread,
            text,
            attach: attachDrawing,
        })
        if ((draftCache.get(targetThread) || "").trim() === text) {
            draftCache.set(targetThread, "")
            try {
                localStorage.removeItem("codex-draft:" + targetThread)
            } catch {}
        }
        if (selected === targetThread && epoch === sendEpoch) {
            if ($("prompt").value.trim() === text) $("prompt").value = ""
            await poll()
        }
    } catch (e) {
        toast(e.message)
    } finally {
        $("send").disabled = false
        if (selected === targetThread && epoch === sendEpoch)
            $("prompt").focus()
    }
}
$("prompt").onkeydown = (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault()
        $("composer").requestSubmit()
    }
}
document.addEventListener("pointerdown", (e) => {
    if (
        !$("picker").contains(e.target) &&
        !$("conversation").contains(e.target)
    )
        $("picker").hidden = true
})
$("resize").onpointerdown = (event) => {
    event.preventDefault()
    $("resize").setPointerCapture(event.pointerId)
    frame.style.pointerEvents = "none"
    const move = (e) =>
        document.documentElement.style.setProperty(
            "--side-width",
            Math.min(760, Math.max(340, innerWidth - e.clientX)) + "px",
        )
    const done = () => {
        frame.style.pointerEvents = ""
        $("resize").removeEventListener("pointermove", move)
        $("resize").removeEventListener("pointerup", done)
    }
    $("resize").addEventListener("pointermove", move)
    $("resize").addEventListener("pointerup", done)
}
async function boot() {
    for (let i = 0; i < 80; i++) {
        try {
            settings = await api("settings")
            break
        } catch {
            await new Promise((r) => setTimeout(r, 500))
        }
    }
    if (!settings) {
        toast("未能连接本地 Codex 服务，请重新启动工作台。")
        return
    }
    setProject(settings.project)
    let recentsOpen = "true",
        recentScope = "all"
    try {
        recentsOpen = localStorage.getItem("recents-open") ?? "true"
        recentScope = localStorage.getItem("recent-scope") || "all"
    } catch {}
    $("recent-scope").value = recentScope
    setRecentsOpen(recentsOpen !== "false")
    await refreshThreads()
    $("connection").textContent = "本机已连接"
    if (settings.selectedThread) await selectThread(settings.selectedThread)
    else $("picker").hidden = false
    setInterval(poll, 1000)
    setInterval(() => {
        if ($("picker").hidden) refreshThreads().catch(() => {})
    }, 12000)
}
boot().catch((e) => toast(e.message))
