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
        fallback: boolean;
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

    const recordingFor = (context: CanvasRenderingContext2D) => {
        const canvas = context.canvas;
        if (!canvas.classList.contains("kix-canvas-tile-content")) return;
        let recording = recordings.get(canvas);
        if (!recording) {
            recording = { context, commands: [], fallback: false };
            recordings.set(canvas, recording);
            canvases.add(new WeakRef(canvas));
            canvas.setAttribute("data-dad-image-safe", "");
        }
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

    const append = (recording: Recording, command: Command) => {
        if (recording.fallback) return;
        if (recording.commands.length >= limit) {
            // Bound memory for long editing sessions. Restore canonical pixels
            // before falling back to the extension's original CSS behaviour.
            repaint(recording, false);
            recording.fallback = true;
            recording.commands = [];
            recording.context.canvas.removeAttribute("data-dad-image-safe");
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
                        if (recording) append(recording, { name, value });
                        // Fallback replay may have restored the previous state.
                        if (recording?.fallback)
                            Reflect.apply(setter, this, [value]);
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
                    let savedArgs = args;
                    if (
                        name === "drawImage" &&
                        args[0] instanceof HTMLCanvasElement
                    ) {
                        // Canvas sources are mutable; preserve the pixels used
                        // by this draw rather than their future contents.
                        const source = args[0];
                        const copy = document.createElement("canvas");
                        copy.width = source.width;
                        copy.height = source.height;
                        Reflect.apply(original, copy.getContext("2d")!, [
                            source,
                            0,
                            0,
                        ]);
                        savedArgs = [copy, ...args.slice(1)];
                    }
                    const command = { name, args: savedArgs };
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
                    recording.commands = [];
                    recording.fallback = false;
                    this.setAttribute("data-dad-image-safe", "");
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
