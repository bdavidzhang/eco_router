import { create } from 'zustand'
import { REGIONS, MODELS, TaskType } from './sciUtils'

type RegionEntry = typeof REGIONS[number]
type ModelEntry = typeof MODELS['code'][number]

interface GlobeStore {
  regions: typeof REGIONS
  activeRegionId: string
  activeTask: TaskType
  setActiveRegion: (id: string) => void
  setActiveTask: (task: TaskType) => void
  // derived
  activeRegion: () => RegionEntry
  worstSci: () => number
  bestSci: () => number
  savedVsWorst: () => number
  activeModels: () => ModelEntry[]
}

export const useGlobeStore = create<GlobeStore>((set, get) => ({
  regions: REGIONS,
  activeRegionId: 'eu-north',
  activeTask: 'code',
  setActiveRegion: (id) => set({ activeRegionId: id }),
  setActiveTask: (task) => set({ activeTask: task }),
  activeRegion: () => get().regions.find(r => r.id === get().activeRegionId)!,
  worstSci: () => Math.max(...get().regions.map(r => r.sci)),
  bestSci:  () => Math.min(...get().regions.map(r => r.sci)),
  savedVsWorst: () => {
    const r = get().activeRegion()
    return Math.round((1 - r.sci / get().worstSci()) * 100)
  },
  activeModels: () => MODELS[get().activeTask],
}))
