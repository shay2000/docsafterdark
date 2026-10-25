/// <reference lib="es2021.weakref" />
// Runs in the page's MAIN world, before Google Docs caches canvas methods.
// Keep image draws unchanged; apply the existing filter to vector/text draws.
(() => {
    const attribute = "data-dad-canvas-filter";
    const proto = CanvasRenderingContext2D.prototype;
    if (!("filter" in proto) || !("reset" in proto)) return;

    type Command = { name: string; args?: unknown[]; value?: unknown };
    type Recording = {
        context: CanvasRenderingContext2D;
        commands: Command[];
        snapshotBytes: number;
        fallback: boolean;
        resizeObserver: MutationObserver;
    };
    // Do not retain canvases/pages that Google Docs has discarded whilst scrolling.
    const recordings = new WeakMap<HTMLCanvasElement, Recording>();
    const canvases = new Set<WeakRef<HTMLCanvasElement>>();
    const methods = new Map<string, (...args: unknown[]) => unknown>();
    const setters = new Map<string, (value: unknown) => void>();
    const drawing = new Set([
        "fill",
        "stroke",
        "fillRect",
        "strokeRect",
        "fillText",
        "strokeText",
    ]);
    let replaying = false;
    let filter = document.documentElement?.getAttribute(attribute) || "none";
    const limit = 30000;
    // Command count alone does not bound native bitmap allocations.
    const snapshotLimit = 16 * 1024 * 1024;

    const discardHistory = (recording: Recording) => {
        recording.commands = [];
        recording.snapshotBytes = 0;
        recording.fallback = false;
        recording.context.canvas.removeAttribute("data-dad-canvas-fallback");
        recording.context.canvas.setAttribute("data-dad-image-safe", "");
    };

    const flushResize = (recording: Recording) => {
        if (recording.resizeObserver.takeRecords().length)
            discardHistory(recording);
    };

    const recordingFor = (context: CanvasRenderingContext2D) => {
        const canvas = context.canvas;
        if (!canvas.classList.contains("kix-canvas-tile-content")) return;
        let recording = recordings.get(canvas);
        if (!recording) {
            // Attribute writes (including Attr.value) bypass the IDL setters.
            // Keep this observer in the weakly-held recording, not a global
            // collection of observed tiles. Drain it before synchronous draws
            // so a later callback cannot discard post-resize commands.
            const resizeObserver = new MutationObserver(() => {
                discardHistory(recordings.get(canvas)!);
            });
            recording = {
                context,
                commands: [],
                snapshotBytes: 0,
                fallback: false,
                resizeObserver,
            };
            recordings.set(canvas, recording);
            resizeObserver.observe(canvas, {
                attributes: true,
                attributeFilter: ["width", "height"],
            });
            canvases.add(new WeakRef(canvas));
            canvas.setAttribute("data-dad-image-safe", "");
        }
        flushResize(recording);
        return recording;
    };

    const invoke = (
        context: CanvasRenderingContext2D,
        name: string,
        args: unknown[],
        enabled: boolean
    ) => {
        const original = methods.get(name)!;
        if (!enabled || !drawing.has(name) || filter === "none") {
            return Reflect.apply(original, context, args);
        }
        const previous = context.filter;
        // Preserve the caller's canvas state, even when the native call throws.
        Reflect.apply(setters.get("filter")!, context, [
            previous === "none" ? filter : `${previous} ${filter}`,
        ]);
        try {
            return Reflect.apply(original, context, args);
        } finally {
            Reflect.apply(setters.get("filter")!, context, [previous]);
        }
    };

    const repaint = (recording: Recording, enabled: boolean) => {
        replaying = true;
        try {
            Reflect.apply(methods.get("reset")!, recording.context, []);
            for (const command of recording.commands) {
                if (command.args) {
                    invoke(
                        recording.context,
                        command.name,
                        command.args,
                        enabled
                    );
                } else {
                    Reflect.apply(
                        setters.get(command.name)!,
                        recording.context,
                        [command.value]
                    );
                }
            }
        } finally {
            replaying = false;
        }
    };

    const fallBack = (recording: Recording, reason = "resource") => {
        if (recording.fallback) return;
        // Restore canonical pixels before using the original CSS behaviour.
        repaint(recording, false);
        recording.fallback = true;
        recording.commands = [];
        recording.snapshotBytes = 0;
        recording.context.canvas.removeAttribute("data-dad-image-safe");
        recording.context.canvas.setAttribute(
            "data-dad-canvas-fallback",
            reason
        );
    };

    const append = (recording: Recording, command: Command) => {
        if (recording.fallback) return;
        if (recording.commands.length >= limit) {
            fallBack(recording, "commands");
            return;
        }
        recording.commands.push(command);
    };

    for (const name of Object.getOwnPropertyNames(proto)) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, name)!;
        if (descriptor.set) {
            const setter = descriptor.set;
            setters.set(name, setter);
            Object.defineProperty(proto, name, {
                ...descriptor,
                set(this: CanvasRenderingContext2D, value: unknown) {
                    // Validate through the native setter before recording it.
                    Reflect.apply(setter, this, [value]);
                    if (!replaying) {
                        const recording = recordingFor(this);
                        // Record the accepted native value, not a caller-owned
                        // coercible object or an ignored invalid assignment.
                        const accepted = Reflect.get(proto, name, this);
                        if (recording) {
                            // Gradients/patterns cannot be cloned faithfully.
                            // Fall back before they can enter the replay log.
                            if (
                                (typeof accepted === "object" &&
                                    accepted !== null) ||
                                (name === "globalCompositeOperation" &&
                                    accepted !== "source-over")
                            )
                                fallBack(
                                    recording,
                                    name === "globalCompositeOperation"
                                        ? "composition"
                                        : "resource"
                                );
                            else append(recording, { name, value: accepted });
                        }
                        // Fallback replay may have restored the previous state.
                        if (recording?.fallback)
                            Reflect.apply(setter, this, [accepted]);
                    }
                },
            });
        } else if (
            typeof descriptor.value === "function" &&
            !/^(constructor|get|create|measure|is)/.test(name)
        ) {
            const original = descriptor.value;
            methods.set(name, original);
            Object.defineProperty(proto, name, {
                ...descriptor,
                value(this: CanvasRenderingContext2D, ...args: unknown[]) {
                    if (replaying) return Reflect.apply(original, this, args);
                    const recording = recordingFor(this);
                    if (!recording) return Reflect.apply(original, this, args);
                    if (name === "reset") {
                        // Native reset discards the bitmap, path and entire state
                        // stack. Nothing before it can affect a later replay.
                        const result = Reflect.apply(original, this, args);
                        discardHistory(recording);
                        return result;
                    }
                    if (recording.fallback)
                        return Reflect.apply(original, this, args);
                    // Check capacity before allocating a snapshot that append()
                    // would immediately discard when the command cap is hit.
                    if (recording.commands.length >= limit) {
                        fallBack(recording, "commands");
                        return Reflect.apply(original, this, args);
                    }
                    if (name === "setLineDash") {
                        // The native getter returns a detached, validated copy.
                        const result = Reflect.apply(original, this, args);
                        append(recording, { name, args: [this.getLineDash()] });
                        return result;
                    }
                    const isResource = (value: unknown) =>
                        (typeof value === "object" && value !== null) ||
                        typeof value === "function";
                    const source = args[0];
                    const snapshotSource =
                        name === "drawImage" &&
                        (source instanceof HTMLCanvasElement ||
                            source instanceof HTMLImageElement);
                    if (
                        (snapshotSource ? args.slice(1) : args).some(isResource)
                    ) {
                        // Path2D, ImageData, OffscreenCanvas, video/bitmap and
                        // other mutable objects stay on the native legacy path.
                        // Restore canonical pixels BEFORE executing this call.
                        fallBack(recording);
                        return Reflect.apply(original, this, args);
                    }
                    let savedArgs = args;
                    let snapshotBytes = 0;
                    if (snapshotSource) {
                        // Canvas sources are mutable; preserve the pixels used
                        // by this draw rather than their future contents.
                        const width =
                            source instanceof HTMLImageElement
                                ? source.naturalWidth
                                : source.width;
                        const height =
                            source instanceof HTMLImageElement
                                ? source.naturalHeight
                                : source.height;
                        snapshotBytes = width * height * 4;
                        if (
                            !width ||
                            !height ||
                            snapshotBytes >
                                snapshotLimit - recording.snapshotBytes
                        ) {
                            fallBack(recording, "snapshot-budget-or-size");
                            return Reflect.apply(original, this, args);
                        }
                        const copy = document.createElement("canvas");
                        copy.width = width;
                        copy.height = height;
                        Reflect.apply(original, copy.getContext("2d")!, [
                            source,
                            0,
                            0,
                        ]);
                        savedArgs = [copy, ...args.slice(1)];
                    }
                    const command = { name, args: savedArgs };
                    recording.snapshotBytes += snapshotBytes;
                    append(recording, command);
                    try {
                        return invoke(this, name, args, !recording.fallback);
                    } catch (error) {
                        // Invalid native calls must not recur on theme changes.
                        if (
                            recording.commands[
                                recording.commands.length - 1
                            ] === command
                        ) {
                            recording.commands.pop();
                            recording.snapshotBytes -= snapshotBytes;
                        }
                        throw error;
                    }
                },
            });
        }
    }

    // A canvas resize discards its bitmap, paths and state, so discard its log.
    for (const name of ["width", "height"]) {
        const descriptor = Object.getOwnPropertyDescriptor(
            HTMLCanvasElement.prototype,
            name
        )!;
        Object.defineProperty(HTMLCanvasElement.prototype, name, {
            ...descriptor,
            set(this: HTMLCanvasElement, value: number) {
                Reflect.apply(descriptor.set!, this, [value]);
                const recording = recordings.get(this);
                if (recording) {
                    recording.resizeObserver.takeRecords();
                    discardHistory(recording);
                }
            },
        });
    }

    const observe = () => {
        const observer = new MutationObserver(() => {
            const next =
                document.documentElement.getAttribute(attribute) || "none";
            if (next === filter) return;
            filter = next;
            for (const reference of canvases) {
                const canvas = reference.deref();
                if (!canvas) {
                    canvases.delete(reference);
                } else if (canvas.isConnected) {
                    const recording = recordings.get(canvas)!;
                    flushResize(recording);
                    if (!recording.fallback) repaint(recording, true);
                }
            }
        });
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: [attribute],
        });
    };
    if (document.documentElement) observe();
    else document.addEventListener("DOMContentLoaded", observe, { once: true });
})();
