<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.subtitle') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="budget-header">
          <label for="budget-slider" class="budget-label">{{ t('restocking.budget') }}</label>
          <span class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</span>
        </div>
        <input
          id="budget-slider"
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="maxBudget"
          :step="STEP"
        />
        <div class="budget-scale">
          <span>{{ formatCurrency(0, currentCurrency) }}</span>
          <span>{{ formatCurrency(maxBudget, currentCurrency) }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.stats.selected') }}</div>
          <div class="stat-value">{{ selectedItems.length }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.stats.selectedTotal') }}</div>
          <div class="stat-value">{{ formatCurrency(selectedTotal, currentCurrency) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.stats.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remainingBudget, currentCurrency) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.stats.longestLead') }}</div>
          <div class="stat-value">{{ t('restocking.days', { days: longestLeadTime }) }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.candidates') }} ({{ selectableCount }} {{ t('restocking.selectable') }} / {{ candidates.length }})</h3>
          <button
            type="button"
            class="primary-btn"
            :disabled="selectedItems.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="successMessage" class="inline-success" role="status">{{ successMessage }}</div>
        <div v-if="submitError" class="inline-error" role="alert">{{ submitError }}</div>

        <div v-if="candidates.length === 0" class="empty">{{ t('restocking.empty') }}</div>
        <div v-else class="table-container">
          <table class="restocking-table">
            <thead>
              <tr>
                <th class="col-check"><span class="sr-only">{{ t('restocking.select') }}</span></th>
                <th>{{ t('restocking.columns.item') }}</th>
                <th class="num">{{ t('restocking.columns.forecastedDemand') }}</th>
                <th class="num">{{ t('restocking.columns.onHand') }}</th>
                <th class="num">{{ t('restocking.columns.shortfall') }}</th>
                <th class="num">{{ t('restocking.columns.unitCost') }}</th>
                <th class="num">{{ t('restocking.columns.totalCost') }}</th>
                <th class="num">{{ t('restocking.columns.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in candidates"
                :key="item.sku"
                :class="{ dimmed: !isSelectable(item) }"
              >
                <td class="col-check">
                  <input
                    type="checkbox"
                    :aria-label="t('restocking.select') + ' ' + item.sku"
                    :checked="selectedSkus.includes(item.sku)"
                    :disabled="!isSelectable(item) || submitting"
                    @change="toggle(item.sku)"
                  />
                </td>
                <td>
                  <div class="item-sku"><strong>{{ item.sku }}</strong></div>
                  <div class="item-name">
                    {{ translateProductName(item.name) }}
                    <span v-if="!isSelectable(item)" class="badge danger over-budget">{{ t('restocking.overBudget') }}</span>
                    <span v-if="suggestedSkus.has(item.sku)" class="badge suggested">{{ t('restocking.suggested') }}</span>
                  </div>
                </td>
                <td class="num">{{ item.forecasted_demand.toLocaleString() }}</td>
                <td class="num">{{ item.quantity_on_hand.toLocaleString() }}</td>
                <td class="num">{{ item.shortfall.toLocaleString() }}</td>
                <td class="num">{{ formatCurrencyWithDecimals(item.unit_cost, currentCurrency, 2) }}</td>
                <td class="num"><strong>{{ formatCurrency(item.total_cost, currentCurrency) }}</strong></td>
                <td class="num">{{ item.lead_time_days }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { useFilters } from '../composables/useFilters'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const STEP = 1000

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()
    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const candidates = ref([])
    const budget = ref(0)
    const selectedSkus = ref([])
    const submitting = ref(false)
    const submitError = ref(null)
    const successMessage = ref(null)

    // Slider max: total of all candidates rounded up to the step
    const maxBudget = computed(() => {
      const total = candidates.value.reduce((sum, c) => sum + c.total_cost, 0)
      return Math.ceil(total / STEP) * STEP
    })

    // Non-binding hint: greedy in shortfall order, include if full cost fits
    const suggested = computed(() => {
      let remaining = budget.value
      const result = []
      for (const item of candidates.value) {
        if (item.total_cost <= remaining) {
          result.push(item)
          remaining -= item.total_cost
        }
      }
      return result
    })

    const suggestedSkus = computed(() => new Set(suggested.value.map(r => r.sku)))

    const selectedItems = computed(() =>
      candidates.value.filter(item => selectedSkus.value.includes(item.sku))
    )
    const selectedTotal = computed(() =>
      selectedItems.value.reduce((sum, i) => sum + i.total_cost, 0)
    )
    const remainingBudget = computed(() => budget.value - selectedTotal.value)
    const longestLeadTime = computed(() =>
      selectedItems.value.reduce((max, i) => Math.max(max, i.lead_time_days), 0)
    )

    // Selected items stay enabled; others must fit in the budget left after current picks
    const isSelectable = (item) =>
      selectedSkus.value.includes(item.sku) || item.total_cost <= remainingBudget.value
    const selectableCount = computed(() => candidates.value.filter(isSelectable).length)

    const toggle = (sku) => {
      if (selectedSkus.value.includes(sku)) {
        selectedSkus.value = selectedSkus.value.filter(s => s !== sku)
        return
      }
      const item = candidates.value.find(c => c.sku === sku)
      if (item && item.total_cost <= remainingBudget.value) {
        selectedSkus.value = [...selectedSkus.value, sku]
      }
    }

    // If budget drops below the selection, drop lowest-ranked (latest in order) first
    watch([budget, candidates], () => {
      if (selectedTotal.value <= budget.value) return
      const kept = [...selectedItems.value]
      let total = selectedTotal.value
      while (kept.length > 0 && total > budget.value) {
        total -= kept.pop().total_cost
      }
      selectedSkus.value = kept.map(i => i.sku)
    })

    // Guards against out-of-order responses when filters change quickly
    let requestId = 0

    const loadCandidates = async (initial = false) => {
      const myId = ++requestId
      try {
        if (initial) loading.value = true
        error.value = null
        const { warehouse, category } = getCurrentFilters()
        const result = await api.getRestockingCandidates({ warehouse, category })
        if (myId !== requestId) return
        // Drop selections no longer present, before candidates change
        const skus = new Set(result.map(c => c.sku))
        selectedSkus.value = selectedSkus.value.filter(s => skus.has(s))
        candidates.value = result
        if (initial) {
          budget.value = Math.round((maxBudget.value * 0.25) / STEP) * STEP
        } else if (budget.value > maxBudget.value) {
          budget.value = maxBudget.value
        }
      } catch (err) {
        if (myId !== requestId) return
        error.value = 'Failed to load restocking candidates: ' + err.message
      } finally {
        if (myId === requestId) loading.value = false
      }
    }

    watch([selectedLocation, selectedCategory], () => loadCandidates())

    const placeOrder = async () => {
      if (selectedItems.value.length === 0 || submitting.value) return
      submitting.value = true
      submitError.value = null
      successMessage.value = null
      try {
        const order = await api.createRestockingOrder({
          items: selectedItems.value.map(i => ({ sku: i.sku, quantity: i.shortfall })),
          budget: budget.value
        })
        successMessage.value = t('restocking.success', {
          orderNumber: order.order_number,
          days: order.lead_time_days
        })
        selectedSkus.value = []
        await loadCandidates()
      } catch (err) {
        submitError.value = err.response?.data?.detail || t('restocking.error')
        console.error('Place order error:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(() => loadCandidates(true))

    return {
      t,
      STEP,
      currentCurrency,
      translateProductName,
      formatCurrency,
      formatCurrencyWithDecimals,
      loading,
      error,
      candidates,
      budget,
      maxBudget,
      suggestedSkus,
      isSelectable,
      selectableCount,
      selectedSkus,
      selectedItems,
      selectedTotal,
      remainingBudget,
      longestLeadTime,
      toggle,
      placeOrder,
      submitting,
      submitError,
      successMessage
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.25rem;
}

.budget-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #0f172a;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.813rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.restocking-table {
  width: 100%;
}

.col-check {
  width: 48px;
  text-align: center;
}

.num {
  text-align: right;
  white-space: nowrap;
}

.item-name {
  font-size: 0.813rem;
  color: #64748b;
}

.over-budget {
  margin-left: 0.5rem;
}

.badge.suggested {
  margin-left: 0.5rem;
  background: #f1f5f9;
  color: #475569;
}

tr.dimmed td {
  opacity: 0.5;
}

.primary-btn {
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.primary-btn:hover:not(:disabled) {
  background: #1e293b;
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.inline-success,
.inline-error {
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.inline-success {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
}

.inline-error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
}

.empty {
  padding: 1rem 0;
  color: #64748b;
  font-size: 0.875rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}
</style>
