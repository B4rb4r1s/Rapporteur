import { readFile, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

// json-schema-to-typescript знает кортежи только в форме items-массива, а заголовки полей
// превращает в отдельные алиасы вида Label1. Схема на диске остаётся в draft 2020-12.
function prepare(node, isProperty = false) {
  if (Array.isArray(node)) return node.map((item) => prepare(item));
  if (node === null || typeof node !== "object") return node;

  const result = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "title" && isProperty) continue;
    if (key === "prefixItems") {
      result.items = prepare(value);
    } else if (key === "properties") {
      result.properties = Object.fromEntries(
        Object.entries(value).map(([name, schema]) => [name, prepare(schema, true)]),
      );
    } else if (key === "$defs") {
      result.$defs = Object.fromEntries(
        Object.entries(value).map(([name, schema]) => [name, prepare(schema)]),
      );
    } else {
      result[key] = prepare(value);
    }
  }
  return result;
}

const schema = JSON.parse(await readFile("schema/all.schema.json", "utf8"));
const banner = `// Сгенерировано из schema/all.schema.json командой make generate. Не редактировать.\n// Версия контрактов: ${schema.version}`;

const types = await compile(prepare(schema), "RapporteurContracts", {
  bannerComment: banner,
  unreachableDefinitions: true,
  additionalProperties: false,
  style: { printWidth: 100 },
});

await writeFile("ts/index.ts", types);
