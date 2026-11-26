// A small Express server. minicloud starts it with `npm start` and sets PORT=80.
const express = require("express");

const app = express();
const port = Number(process.env.PORT) || 3000;

app.get("/", (req, res) => {
  res.send(
    `<h1>Xin chào từ minicloud</h1><p>Node.js ${process.version} với Express, ở cổng ${port}.</p>` +
      `<p><a href="/api/time">/api/time</a></p>`,
  );
});

app.get("/api/time", (req, res) => {
  res.json({ now: new Date().toISOString() });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`listening on ${port}`);
});
