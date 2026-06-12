declare global {
  // Provide a declaration so TypeScript accepts the runtime polyfill assignment.
  interface ObjectConstructor {
    hasOwn?(obj: object, prop: PropertyKey): boolean;
  }
}

// Polyfill Object.hasOwn for browsers (e.g. Chrome ≤ 91) that lack the method.
if (typeof Object.hasOwn !== "function") {
  Object.defineProperty(Object, "hasOwn", {
    configurable: true,
    writable: true,
    value(target: object, property: PropertyKey) {
      if (target === null || (typeof target !== "object" && typeof target !== "function")) {
        throw new TypeError("Object.hasOwn called on non-object");
      }
      return Object.prototype.hasOwnProperty.call(target, property);
    },
  });
}

export {};
