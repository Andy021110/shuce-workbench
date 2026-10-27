<script setup lang="ts">
import { onMounted, ref, watch } from "vue"
import { api } from "../api"
const props = defineProps<{admin: boolean}>()
const emit = defineEmits<{imported: [database: string]; close: []}>()
const sources = ref<any[]>([])
const catalogBusy = ref(true)
const name = ref("")
const description = ref("")
const content = ref("")
const fields = ref<Record<string,string>>({})
const preview = ref<any>(null)
const busy = ref(false)
const error = ref("")
const status = ref("")
const expanded = ref("")
onMounted(async () => { try {sources.value = await api.datasets()} catch(e) {error.value=String(e)} finally {catalogBusy.value=false} })
watch([name, content], () => {preview.value=null; status.value=""})
async function pick(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  error.value=""; preview.value=null
  if (file.size > 10000000) {error.value="CSV 文件不能超过 10 MB"; return}
  try { content.value = new TextDecoder("utf-8", {fatal:true}).decode(await file.arrayBuffer()); name.value=file.name.replace(/\.csv$/i, "").replace(/[^a-zA-Z0-9_]/g,"_"); fields.value={} }
  catch {error.value="文件不是有效的 UTF-8 文本，请转换编码后重试"}
}
async function validate() {
  busy.value=true; error.value=""; status.value="正在校验…"
  try { preview.value=await api.csvPreview({name:name.value,content:content.value}); status.value="校验通过，确认预览与字段说明后导入" }
  catch(e) {error.value=String(e); status.value="校验未通过"}
  finally {busy.value=false}
}
async function ingest() {
  if (!preview.value) return
  busy.value=true; error.value=""; status.value="正在导入…"
  try {const result=await api.csvImport({name:name.value,content:content.value,description:description.value,fields:fields.value}); status.value="导入完成"; sources.value=await api.datasets(); preview.value=null;content.value="";emit('imported', result.database_id)}
  catch(e) {error.value=String(e);status.value="导入失败，可修改后重试"}
  finally {busy.value=false}
}
</script>
<template>
  <section class="dataset-manager">
    <header class="overview-title"><div><h1>数据接入与管理</h1><p>本地 CSV 数据源 · 原始样例始终保留</p></div><button @click="emit('close')">返回工作台</button></header>
    <p v-if="catalogBusy" role="status">正在读取数据源列表…</p>
    <p v-else-if="!sources.length">当前没有可访问的数据源。</p>
    <div v-for="source in sources" :key="source.id" class="source-entry"><button @click="expanded = expanded === source.id ? '' : source.id"><strong>{{ source.id }}</strong><span>{{ source.source }} · {{ source.table_count }} 张表 · 可用</span></button><div v-if="expanded === source.id"><p v-if="source.imported_at">导入于 {{ new Date(source.imported_at).toLocaleString('zh-CN') }}</p><details v-for="table in source.tables" :key="table.id"><summary>{{ table.label }} · {{ table.row_count }} 行</summary><p>{{ table.description }}</p><dl class="field-dictionary"><template v-for="field in table.fields" :key="field.name"><dt>{{ field.name }}</dt><dd>{{ field.description || field.label }}（{{ field.type }}）</dd></template></dl></details></div></div>
    <form v-if="props.admin" class="import-form" @submit.prevent="validate">
      <h2>导入 CSV</h2><p>UTF-8、逗号分隔，最多 10 MB / 100,000 行 / 100 列。表头使用英文、数字或下划线。每次导入创建独立数据源，当前仅管理员可用。暂不提供数据库直连。</p>
      <label>CSV 文件<input type="file" accept=".csv,text/csv" :disabled="busy" @change="pick"></label>
      <label>数据源名称<input v-model.trim="name" required pattern="[a-zA-Z_][a-zA-Z0-9_]*" :disabled="busy" placeholder="例如 monthly_orders"></label>
      <label>数据说明<textarea v-model="description" :disabled="busy" maxlength="500" placeholder="来源、时间范围、单位和统计口径"></textarea></label>
      <button type="submit" :disabled="!content || busy">校验并预览</button>
      <p role="status">{{ status }}</p><p v-if="error" role="alert" class="import-error">{{ error }}</p>
      <div v-if="preview"><h3>{{ preview.row_count }} 行 · 预览前 {{ preview.rows.length }} 行</h3><div class="preview-scroll"><table><thead><tr><th v-for="col in preview.columns" :key="col">{{ col }}</th></tr></thead><tbody><tr v-for="(row,index) in preview.rows" :key="index"><td v-for="col in preview.columns" :key="col">{{ row[col] }}</td></tr></tbody></table></div><label v-for="col in preview.columns" :key="col">{{ col }}（{{ preview.types?.[col] || '文本' }}）的业务说明<input v-model="fields[col]" maxlength="500" :placeholder="'说明单位、含义和口径'"></label><button type="button" :disabled="busy" @click="ingest">确认导入 {{ preview.row_count }} 行</button></div>
    </form>
    <p v-else>只有管理员可以导入 CSV。你可以查看当前角色有权访问的表和字段说明。</p>
  </section>
</template>
