import { describe, expect, test } from "bun:test";
import { BAR_RANK, createRestack, RESTACK_CHANNEL, type Restack } from "../src/bar_stack.ts";
import type { EventBus } from "../src/omp-api.ts";

/** omp's EventBus: handlers run synchronously inside `emit`; a throwing handler propagates here (omp itself would swallow it). */
class FakeBus implements EventBus {
  readonly listeners = new Map<string, Set<(data: unknown) => void>>();
  readonly emitted: Array<{ channel: string; data: unknown }> = [];
  emit(channel: string, data: unknown): void {
    this.emitted.push({ channel, data });
    for (const handler of [...(this.listeners.get(channel) ?? [])]) handler(data);
  }
  on(channel: string, handler: (data: unknown) => void): () => void {
    const set = this.listeners.get(channel) ?? new Set();
    set.add(handler);
    this.listeners.set(channel, set);
    return () => void set.delete(handler);
  }
  count(channel = RESTACK_CHANNEL): number {
    return this.listeners.get(channel)?.size ?? 0;
  }
}

describe("constants", () => {
  test("the channel and the ranks are the values both repositories pin", () => {
    expect(RESTACK_CHANNEL).toBe("bars:restack");
    expect(BAR_RANK).toEqual({ swarm: 10, ultrathink: 20 });
    expect(BAR_RANK.swarm).toBeLessThan(BAR_RANK.ultrathink);
  });
});

describe("announce", () => {
  test("emits { v: 1, after: rank } on the restack channel, once per call", () => {
    const bus = new FakeBus();
    const restack = createRestack(bus, { rank: 10, remount: () => {} });
    restack.announce();
    restack.announce();
    expect(bus.emitted).toEqual([
      { channel: "bars:restack", data: { v: 1, after: 10 } },
      { channel: "bars:restack", data: { v: 1, after: 10 } },
    ]);
  });

  test("the rank is the caller's, not a constant", () => {
    const bus = new FakeBus();
    createRestack(bus, { rank: 20, remount: () => {} }).announce();
    expect(bus.emitted.map((entry) => entry.data)).toEqual([{ v: 1, after: 20 }]);
  });

  test("a bar never answers its own announcement", () => {
    const bus = new FakeBus();
    let remounts = 0;
    const restack = createRestack(bus, { rank: 10, remount: () => void remounts++ });
    restack.announce();
    expect(remounts).toBe(0);
  });

  test("a bus whose emit throws does not make announce throw", () => {
    const bus: Pick<EventBus, "emit" | "on"> = {
      on: () => () => {},
      emit: () => {
        throw new Error("bus down");
      },
    };
    expect(() => createRestack(bus, { rank: 10, remount: () => {} }).announce()).not.toThrow();
  });
});

describe("missing or partial buses", () => {
  test("no bus: every call is a no-op and nothing throws", () => {
    const restack = createRestack(undefined, { rank: 10, remount: () => {} });
    expect(() => {
      restack.announce();
      restack.dispose();
      restack.dispose();
    }).not.toThrow();
  });

  test("a bus without on or emit never throws, whichever half is missing", () => {
    const buses = [{}, { emit() {} }, { on: () => () => {} }, { on: undefined, emit: undefined }] as unknown as Array<Pick<EventBus, "emit" | "on">>;
    for (const [index, bus] of buses.entries()) {
      let error: unknown;
      try {
        const restack = createRestack(bus, { rank: 10, remount: () => {} });
        restack.announce();
        restack.dispose();
      } catch (caught) {
        error = caught;
      }
      expect({ bus: index, error }).toEqual({ bus: index, error: undefined });
    }
  });

  test("a bus whose on throws still lets the bar announce", () => {
    const emitted: unknown[] = [];
    const bus: Pick<EventBus, "emit" | "on"> = {
      on: () => {
        throw new Error("no subscriptions");
      },
      emit: (_channel, data) => void emitted.push(data),
    };
    const restack = createRestack(bus, { rank: 10, remount: () => {} });
    restack.announce();
    expect(emitted).toEqual([{ v: 1, after: 10 }]);
    expect(() => restack.dispose()).not.toThrow();
  });

  test("a bus whose on returns something that is not a function does not break dispose", () => {
    const bus = { on: () => 7, emit() {} } as unknown as Pick<EventBus, "emit" | "on">;
    const restack = createRestack(bus, { rank: 10, remount: () => {} });
    expect(() => restack.dispose()).not.toThrow();
  });

  test("an unsubscribe that throws is swallowed by dispose", () => {
    const bus: Pick<EventBus, "emit" | "on"> = {
      emit() {},
      on: () => () => {
        throw new Error("already gone");
      },
    };
    expect(() => createRestack(bus, { rank: 10, remount: () => {} }).dispose()).not.toThrow();
  });
});

describe("what triggers a remount", () => {
  function listening(rank = 20) {
    const bus = new FakeBus();
    const remounts: number[] = [];
    const restack = createRestack(bus, { rank, remount: () => void remounts.push(remounts.length) });
    return { bus, restack, remounts };
  }

  test("exactly one subscription, on the restack channel only", () => {
    const { bus } = listening();
    expect([...bus.listeners.keys()]).toEqual([RESTACK_CHANNEL]);
    expect(bus.count()).toBe(1);
  });

  test("a valid announcement of a lower rank remounts once", () => {
    const { bus, remounts } = listening(20);
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(remounts).toHaveLength(1);
  });

  test("lower means strictly lower, including 0, negatives and fractions", () => {
    for (const after of [0, -5, 19, 19.5, 10]) {
      const { bus, remounts } = listening(20);
      bus.emit(RESTACK_CHANNEL, { v: 1, after });
      expect({ after, remounts: remounts.length }).toEqual({ after, remounts: 1 });
    }
  });

  test("an announcement of an equal or higher rank is ignored", () => {
    for (const after of [20, 20.0001, 21, 1000, 1e9]) {
      const { bus, remounts } = listening(20);
      bus.emit(RESTACK_CHANNEL, { v: 1, after });
      expect({ after, remounts: remounts.length }).toEqual({ after, remounts: 0 });
    }
  });

  test("extra fields on a valid payload do not matter", () => {
    const { bus, remounts } = listening(20);
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10, from: "swarm" });
    expect(remounts).toHaveLength(1);
  });

  test("a payload that is not an object is ignored", () => {
    for (const payload of [undefined, null, 3, 0, "x", "", true, false, Symbol("s"), 10n, () => ({ v: 1, after: 10 })]) {
      const { bus, remounts } = listening(20);
      expect(() => bus.emit(RESTACK_CHANNEL, payload)).not.toThrow();
      expect({ payload: String(payload), remounts: remounts.length }).toEqual({ payload: String(payload), remounts: 0 });
    }
  });

  test("another protocol version is ignored", () => {
    for (const v of [0, 2, -1, "1", true, null, undefined, 1.5, [1]]) {
      const { bus, remounts } = listening(20);
      bus.emit(RESTACK_CHANNEL, { v, after: 10 });
      expect({ v, remounts: remounts.length }).toEqual({ v, remounts: 0 });
    }
    const { bus, remounts } = listening(20);
    bus.emit(RESTACK_CHANNEL, { after: 10 });
    expect(remounts).toHaveLength(0);
  });

  test("an after that is not a finite number is ignored", () => {
    for (const after of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, "10", "", null, undefined, true, [], {}, 10n]) {
      const { bus, remounts } = listening(20);
      bus.emit(RESTACK_CHANNEL, { v: 1, after });
      expect({ after: String(after), remounts: remounts.length }).toEqual({ after: String(after), remounts: 0 });
    }
    const { bus, remounts } = listening(20);
    bus.emit(RESTACK_CHANNEL, { v: 1 });
    expect(remounts).toHaveLength(0);
  });
});

describe("re-entrancy and failures", () => {
  test("a nested trigger while a remount runs is a no-op, and the flag resets afterwards", () => {
    const bus = new FakeBus();
    let remounts = 0;
    createRestack(bus, {
      rank: 20,
      remount: () => {
        remounts++;
        bus.emit(RESTACK_CHANNEL, { v: 1, after: 5 }); // another lower-rank announcement arrives mid-remount
        bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
      },
    });
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(remounts).toBe(1);
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(remounts).toBe(2);
  });

  test("a remount that throws is swallowed, and the next announcement still gets through", () => {
    const bus = new FakeBus();
    let remounts = 0;
    createRestack(bus, {
      rank: 20,
      remount: () => {
        remounts++;
        throw new Error("widget host failed");
      },
    });
    expect(() => bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 })).not.toThrow();
    expect(() => bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 })).not.toThrow();
    expect(remounts).toBe(2);
  });

  test("a throwing remount does not stop the other bars on the bus from hearing the announcement", () => {
    const bus = new FakeBus();
    const heard: string[] = [];
    createRestack(bus, {
      rank: 20,
      remount: () => {
        throw new Error("nope");
      },
    });
    bus.on(RESTACK_CHANNEL, () => void heard.push("later listener"));
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(heard).toEqual(["later listener"]);
  });
});

describe("dispose", () => {
  test("unsubscribes, so a lower-rank announcement no longer remounts", () => {
    const bus = new FakeBus();
    let remounts = 0;
    const restack = createRestack(bus, { rank: 20, remount: () => void remounts++ });
    expect(bus.count()).toBe(1);
    restack.dispose();
    expect(bus.count()).toBe(0);
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(remounts).toBe(0);
  });

  test("announce after dispose emits nothing", () => {
    const bus = new FakeBus();
    const restack = createRestack(bus, { rank: 10, remount: () => {} });
    restack.dispose();
    restack.announce();
    expect(bus.emitted).toEqual([]);
  });

  test("dispose twice unsubscribes once and leaves other listeners alone", () => {
    const bus = new FakeBus();
    let other = 0;
    bus.on(RESTACK_CHANNEL, () => void other++);
    const restack = createRestack(bus, { rank: 20, remount: () => {} });
    restack.dispose();
    restack.dispose();
    expect(bus.count()).toBe(1);
    bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 });
    expect(other).toBe(1);
  });

  test("a remount already in flight when dispose runs does not fail", () => {
    const bus = new FakeBus();
    const holder: { restack?: Restack } = {};
    holder.restack = createRestack(bus, {
      rank: 20,
      remount: () => {
        holder.restack?.dispose();
        holder.restack?.announce();
      },
    });
    expect(() => bus.emit(RESTACK_CHANNEL, { v: 1, after: 10 })).not.toThrow();
    expect(bus.count()).toBe(0);
    // only the trigger itself went over the bus: the announce after dispose was dropped
    expect(bus.emitted.map((entry) => entry.data)).toEqual([{ v: 1, after: 10 }]);
  });
});

describe("the protocol on one bus: the stack ends ordered by rank whichever bar loads first", () => {
  /** omp's widget stack: insertion order, re-setting a key moves it to the bottom. */
  function stackOf(bus: FakeBus) {
    const keys: string[] = [];
    const mounts: Record<string, number> = {};
    const set = (key: string): void => {
      const at = keys.indexOf(key);
      if (at >= 0) keys.splice(at, 1);
      keys.push(key);
      mounts[key] = (mounts[key] ?? 0) + 1;
    };
    /** A bar like the two real ones: `mountAndAnnounce()` sets its widget THEN announces; a lower-rank announcement remounts it. */
    function bar(key: string, rank: number) {
      let mounted = false;
      const restack = createRestack(bus, {
        rank,
        remount: () => {
          if (mounted) mountAndAnnounce();
        },
      });
      function mountAndAnnounce(): void {
        set(key);
        restack.announce();
      }
      return {
        mount() {
          mounted = true;
          mountAndAnnounce();
        },
        remount: mountAndAnnounce,
        restack,
      };
    }
    return { keys, mounts, set, bar };
  }

  test("swarm first, Ultrathink second", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const swarm = stack.bar("swarm", BAR_RANK.swarm);
    const ultrathink = stack.bar("ultrathink", BAR_RANK.ultrathink);
    swarm.mount();
    ultrathink.mount();
    expect(stack.keys).toEqual(["swarm", "ultrathink"]);
  });

  test("Ultrathink first, swarm second: the swarm mount pushes Ultrathink back below it", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const swarm = stack.bar("swarm", BAR_RANK.swarm);
    const ultrathink = stack.bar("ultrathink", BAR_RANK.ultrathink);
    ultrathink.mount();
    swarm.mount();
    expect(stack.keys).toEqual(["swarm", "ultrathink"]);
    expect(stack.mounts).toEqual({ swarm: 1, ultrathink: 2 });
  });

  test("a deferred remount of either bar keeps the order", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const swarm = stack.bar("swarm", BAR_RANK.swarm);
    const ultrathink = stack.bar("ultrathink", BAR_RANK.ultrathink);
    ultrathink.mount();
    swarm.mount();
    ultrathink.remount();
    expect(stack.keys).toEqual(["swarm", "ultrathink"]);
    stack.set("third-party");
    swarm.remount();
    expect(stack.keys.filter((key) => key !== "third-party")).toEqual(["swarm", "ultrathink"]);
  });

  test("a higher-rank bar re-mounting alone never makes a lower-rank bar remount", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const swarm = stack.bar("swarm", BAR_RANK.swarm);
    const ultrathink = stack.bar("ultrathink", BAR_RANK.ultrathink);
    swarm.mount();
    ultrathink.mount();
    const before = stack.mounts.swarm;
    for (let i = 0; i < 5; i++) ultrathink.remount();
    expect(stack.mounts.swarm).toBe(before);
  });

  test("three ranks end in rank order for every load order, and the chain terminates", () => {
    const orders: number[][] = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]];
    for (const order of orders) {
      const bus = new FakeBus();
      const stack = stackOf(bus);
      const bars = [stack.bar("a", 10), stack.bar("b", 20), stack.bar("c", 30)];
      for (const index of order) bars[index].mount();
      expect({ order, keys: stack.keys }).toEqual({ order, keys: ["a", "b", "c"] });
    }
  });

  test("two bars of the same rank never answer each other", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const one = stack.bar("one", 10);
    const two = stack.bar("two", 10);
    one.mount();
    two.mount();
    expect(stack.mounts).toEqual({ one: 1, two: 1 });
  });

  test("a bar that is not mounted (closed) does not remount on an announcement", () => {
    const bus = new FakeBus();
    const stack = stackOf(bus);
    const swarm = stack.bar("swarm", BAR_RANK.swarm);
    stack.bar("ultrathink", BAR_RANK.ultrathink); // loaded, never mounted
    swarm.mount();
    expect(stack.keys).toEqual(["swarm"]);
  });
});
