import axios from "axios";

const res = await axios.get(
  "https://www.fpljoe.com/competitions/premier-league/2026-27/fixtures/2"
);

console.log(res.status);
console.log(res.data.slice(0, 1000));