const EventEmitter = require('events');

class MessageBus extends EventEmitter {
  // Fire-and-forget: emit an event, no response expected
  fire(event, payload) {
    this.emit(event, payload);
  }

  // Query: emit an event and wait for a response (Promise-based)
  query(event, payload, timeoutMs = 3000) {
    return new Promise((resolve, reject) => {
      const responseEvent = `${event}_response_${Date.now()}_${Math.random()}`;
      let timeout = setTimeout(() => {
        this.removeAllListeners(responseEvent);
        reject(new Error(`Timeout waiting for response to event '${event}'`));
      }, timeoutMs);
      this.once(responseEvent, (result) => {
        clearTimeout(timeout);
        resolve(result);
      });
      this.emit(event, payload, (result) => {
        this.emit(responseEvent, result);
      });
    });
  }
}

module.exports = MessageBus;
