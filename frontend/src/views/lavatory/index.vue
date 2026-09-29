<template>
  <section class="page" data-module="lavatory">
    <header class="page-head">
      <div>
        <h2>清水排污管理</h2>
        <p class="page-desc">维护排污任务，围绕排污编号、对应航班、清水加注量、排污量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">登记排污任务</button>
        <button class="btn" type="button" @click="exportRows">导出清水排污清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="summary-panel">
      <header class="summary-head">
        <h3>班次汇总</h3>
        <span class="summary-scope">{{ summary.scope }}</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in summaryColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="shift in summary.shifts" :key="shift.班次">
            <td>{{ shift.班次 }}</td>
            <td>{{ shift.服务趟数 }}</td>
            <td>{{ shift.清水加注总量 }}</td>
            <td>{{ shift.排污总量 }}</td>
          </tr>
          <tr v-if="summary.shifts.length">
            <td><strong>合计</strong></td>
            <td><strong>{{ summary.totals.服务趟数 }}</strong></td>
            <td><strong>{{ summary.totals.清水加注总量 }}</strong></td>
            <td><strong>{{ summary.totals.排污总量 }}</strong></td>
          </tr>
          <tr v-if="!summary.shifts.length">
            <td :colspan="summaryColumns.length" class="empty-state">暂无已完成服务，班次汇总为空</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form v-if="createVisible" class="filter-bar" @submit.prevent="submitCreate">
      <label v-for="field in createFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="createForm[field]" :list="field === '服务车辆' ? 'lavatory-vehicles' : undefined" :placeholder="`填写${field}`" />
      </label>
      <datalist id="lavatory-vehicles">
        <option v-for="vehicle in vehicles" :key="vehicle" :value="vehicle" />
      </datalist>
      <button class="btn primary" type="submit">提交登记</button>
      <button class="btn ghost" type="button" @click="toggleCreate">取消</button>
    </form>

    <form v-if="editTarget" class="filter-bar" @submit.prevent="submitEdit">
      <span class="edit-title">修改 {{ editTarget.排污编号 }} 的量值</span>
      <label v-for="field in editableFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="editForm[field]" :placeholder="`填写${field}`" />
      </label>
      <button class="btn primary" type="submit">保存修改</button>
      <button class="btn ghost" type="button" @click="editTarget = null">取消</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openEdit(row)">修改量值</button>
            <button class="link" type="button" @click="openDetail(row)">详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无清水排污数据，可先登记排污任务</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <header class="summary-head">
        <h3>详情：{{ detail.排污编号 }}</h3>
        <button class="btn ghost" type="button" @click="detail = null">收起</button>
      </header>
      <table class="data-table">
        <tbody>
          <tr v-for="column in columns" :key="column">
            <th>{{ column }}</th>
            <td>{{ detail[column] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条清水排污记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ShiftRow = { 班次: string; 服务趟数: number; 清水加注总量: number; 排污总量: number }
type Summary = {
  scope: string
  status_counts: Record<string, number>
  shifts: ShiftRow[]
  totals: { 服务趟数: number; 清水加注总量: number; 排污总量: number }
}

const ENDPOINT = '/api/lavatory'
const columns = ["排污编号", "对应航班", "清水加注量", "排污量", "服务车辆", "操作人员", "完成时间", "服务状态"]
const actions = ["开始服务", "完成服务", "取消服务"]
const statuses = ["待服务", "服务中", "已完成", "已取消"]
const summaryColumns = ["班次", "服务趟数", "清水加注总量", "排污总量"]
const createFields = ["排污编号", "对应航班", "清水加注量", "排污量", "服务车辆", "操作人员", "完成时间"]
const editableFields = ["清水加注量", "排污量"]
const vehicles = ["清水车-01", "清水车-02", "清水车-03"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref(statuses.map((status) => ({ label: `${status}航班`, value: 0 })))
const summary = ref<Summary>({ scope: '', status_counts: {}, shifts: [], totals: { 服务趟数: 0, 清水加注总量: 0, 排污总量: 0 } })
const createVisible = ref(false)
const createForm = reactive<Record<string, string>>({})
const editTarget = ref<Row | null>(null)
const editForm = reactive<Record<string, string>>({ 清水加注量: '', 排污量: '' })
const detail = ref<Row | null>(null)

/** 服务端返回的错误原样带出来：ActionResult.message 或 FastAPI 的 detail，不做二次包装。 */
async function parseBody(response: Response): Promise<Record<string, unknown> | null> {
  try {
    return (await response.json()) as Record<string, unknown>
  } catch {
    return null
  }
}

function bodyMessage(body: Record<string, unknown> | null): string | null {
  if (!body) return null
  if (typeof body.message === 'string') return body.message
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail) && body.detail.length) {
    return body.detail.map((item: { msg?: string }) => item.msg ?? String(item)).join('；')
  }
  return null
}

/** 动作/登记/修改共用的响应处理：HTTP 非 2xx 或 ActionResult.ok=false 都视为失败。 */
async function ensureOk(response: Response, fallback: string): Promise<void> {
  const body = await parseBody(response)
  if (!response.ok) {
    throw new Error(bodyMessage(body) ?? `接口返回 ${response.status}`)
  }
  if (body && body.ok === false) {
    throw new Error(bodyMessage(body) ?? fallback)
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function toggleCreate() {
  createVisible.value = !createVisible.value
  if (!createVisible.value) {
    Object.keys(createForm).forEach((field) => delete createForm[field])
  }
}

async function refreshAll() {
  await Promise.all([reload(), reloadSummary()])
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    await ensureOk(response, '排污任务登记未生效')
    toggleCreate()
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排污任务登记失败'
  }
}

function openEdit(row: Row) {
  editTarget.value = row
  editForm.清水加注量 = String(row.清水加注量 ?? '')
  editForm.排污量 = String(row.排污量 ?? '')
}

async function submitEdit() {
  if (!editTarget.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${editTarget.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: { ...editForm } }),
    })
    await ensureOk(response, '清水排污量值修改未生效')
    editTarget.value = null
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清水排污量值修改失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(bodyMessage(await parseBody(response)) ?? `接口返回 ${response.status}`)
    }
    detail.value = (await response.json()) as Row
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排污任务详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    await ensureOk(response, '清水排污动作未生效')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清水排污操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error(bodyMessage(await parseBody(response)) ?? `接口返回 ${response.status}`)
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清水排污列表读取失败'
  }
}

async function reloadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) {
      throw new Error(bodyMessage(await parseBody(response)) ?? `接口返回 ${response.status}`)
    }
    const payload = (await response.json()) as Summary
    summary.value = payload
    stats.value = statuses.map((status) => ({
      label: `${status}航班`,
      value: payload.status_counts?.[status] ?? 0,
    }))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '班次汇总读取失败'
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.summary-panel,
.detail-panel {
  margin-bottom: 12px;
}
.summary-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}
.summary-head h3 {
  margin: 8px 0;
  font-size: 14px;
}
.summary-scope {
  color: var(--muted);
  font-size: 12px;
}
.edit-title {
  font-size: 13px;
  color: var(--muted);
  align-self: center;
}
</style>
