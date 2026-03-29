import { forwardRef, useImperativeHandle, useRef } from 'react'
import * as THREE from 'three'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Stars } from '@react-three/drei'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { GlobeMesh } from './GlobeMesh'
import { RegionMarker } from './RegionMarker'
import { REGIONS, latLonToVector3 } from './sciUtils'

// ── Inner scene component that has access to the R3F context ─────────────────

interface SceneInnerProps {
  orbitRef: React.MutableRefObject<OrbitControlsImpl | null>
  flyTarget: React.MutableRefObject<{ phi: number; theta: number; radius: number } | null>
  flyProgress: React.MutableRefObject<number>
}

function SceneInner({ orbitRef, flyTarget, flyProgress }: SceneInnerProps) {
  const { camera } = useThree()

  useFrame(() => {
    if (!flyTarget.current || !orbitRef.current) return

    flyProgress.current = Math.min(flyProgress.current + 0.04, 1)

    const spherical = new THREE.Spherical().setFromVector3(camera.position)

    // Lerp radius
    spherical.radius += (flyTarget.current.radius - spherical.radius) * 0.04

    // Lerp phi (polar angle)
    spherical.phi += (flyTarget.current.phi - spherical.phi) * 0.04

    // Lerp theta (azimuthal) — take the short way around to avoid 330° spin
    let dTheta = flyTarget.current.theta - spherical.theta
    if (dTheta >  Math.PI) dTheta -= 2 * Math.PI
    if (dTheta < -Math.PI) dTheta += 2 * Math.PI
    spherical.theta += dTheta * 0.04

    camera.position.setFromSpherical(spherical)
    camera.lookAt(0, 0, 0)
    orbitRef.current.update()

    if (flyProgress.current >= 1) flyTarget.current = null
  })

  return null
}

// ── Public ref interface ──────────────────────────────────────────────────────

export interface GlobeSceneHandle {
  flyTo: (regionId: string) => void
}

// ── GlobeScene ────────────────────────────────────────────────────────────────

export const GlobeScene = forwardRef<GlobeSceneHandle>(function GlobeScene(_, ref) {
  const orbitRef = useRef<OrbitControlsImpl | null>(null)
  const autoRotateTimeout = useRef<ReturnType<typeof setTimeout>>()
  const flyTarget = useRef<{ phi: number; theta: number; radius: number } | null>(null)
  const flyProgress = useRef(0)

  const pauseAutoRotate = () => {
    if (orbitRef.current) orbitRef.current.autoRotate = false
    clearTimeout(autoRotateTimeout.current)
    autoRotateTimeout.current = setTimeout(() => {
      if (orbitRef.current) orbitRef.current.autoRotate = true
    }, 4000)
  }

  useImperativeHandle(ref, () => ({
    flyTo(regionId: string) {
      const region = REGIONS.find(r => r.id === regionId)
      if (!region || !orbitRef.current) return

      const phi   = (90 - region.lat) * (Math.PI / 180)
      const theta = (region.lon + 180) * (Math.PI / 180)

      flyTarget.current  = { phi, theta, radius: 2.8 }
      flyProgress.current = 0
      orbitRef.current.autoRotate = false
    },
  }))

  // Suppress unused-var warning — latLonToVector3 is used indirectly in store
  void latLonToVector3

  return (
    <Canvas
      camera={{ position: [0, 0, 2.8], fov: 45 }}
      gl={{ antialias: true, alpha: true }}
      style={{ background: 'transparent', width: '100%', height: '100%' }}
      onPointerDown={pauseAutoRotate}
    >
      <SceneInner orbitRef={orbitRef} flyTarget={flyTarget} flyProgress={flyProgress} />

      <ambientLight intensity={0.4} />
      <directionalLight position={[5, 3, 5]} intensity={1.2} />

      <Stars radius={100} depth={50} count={3000} factor={3} saturation={0} fade speed={0.5} />

      <GlobeMesh />

      {REGIONS.map(r => (
        <RegionMarker key={r.id} region={r} />
      ))}

      <OrbitControls
        ref={orbitRef}
        enablePan={false}
        enableZoom
        minDistance={1.8}
        maxDistance={5.0}
        enableDamping
        dampingFactor={0.05}
        autoRotate
        autoRotateSpeed={0.4}
      />
    </Canvas>
  )
})
