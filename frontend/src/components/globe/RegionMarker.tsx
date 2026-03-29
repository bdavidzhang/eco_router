import { useRef, useState } from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { latLonToVector3, sciToColor } from './sciUtils'
import { useGlobeStore } from './useGlobeStore'
import type { Region } from './sciUtils'

interface RegionMarkerProps {
  region: Region
}

export function RegionMarker({ region }: RegionMarkerProps) {
  const { activeRegionId, setActiveRegion, worstSci } = useGlobeStore()
  const isActive = region.id === activeRegionId
  const [hovered, setHovered] = useState(false)

  const groupRef = useRef<THREE.Group>(null)
  const ringRef = useRef<THREE.Mesh>(null)
  const pulseProgress = useRef(0)

  const { camera } = useThree()

  const position = latLonToVector3(region.lat, region.lon, 1.05)
  const color = sciToColor(region.sci)
  const maxSci = worstSci()

  // Orient group outward from globe center
  const outwardQuaternion = (() => {
    const q = new THREE.Quaternion()
    const dummy = new THREE.Object3D()
    dummy.position.copy(position)
    dummy.lookAt(new THREE.Vector3(0, 0, 0))
    dummy.rotateY(Math.PI)
    q.copy(dummy.quaternion)
    return q
  })()

  useFrame(() => {
    if (!ringRef.current) return

    // Billboard: ring always faces camera
    ringRef.current.quaternion.copy(camera.quaternion)

    // Pulse animation: scale 1→2.5, opacity 0.8→0 then reset
    pulseProgress.current = (pulseProgress.current + 0.012) % 1
    const p = pulseProgress.current
    const mat = ringRef.current.material as THREE.MeshBasicMaterial
    ringRef.current.scale.setScalar(1 + p * 1.5)
    mat.opacity = 0.8 * (1 - p)
  })

  const sciLabel =
    region.sci < 50  ? 'Low carbon'
    : region.sci < 80  ? 'Medium carbon'
    : region.sci < 110 ? 'High carbon'
    : 'Very high carbon'

  const barWidth = Math.round((region.sci / maxSci) * 100)

  return (
    <group
      ref={groupRef}
      position={position}
      quaternion={outwardQuaternion}
    >
      {/* Inner dot */}
      <mesh>
        <sphereGeometry args={[0.012, 16, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>

      {/* Pulse ring */}
      <mesh ref={ringRef}>
        <ringGeometry args={[0.018, 0.026, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.8} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>

      {/* Active halo */}
      {isActive && (
        <mesh>
          <ringGeometry args={[0.03, 0.036, 32]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.9} side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
      )}

      {/* Invisible hit target */}
      <mesh
        onPointerOver={() => {
          document.body.style.cursor = 'pointer'
          setHovered(true)
        }}
        onPointerOut={() => {
          document.body.style.cursor = ''
          setHovered(false)
        }}
        onClick={() => setActiveRegion(region.id)}
      >
        <sphereGeometry args={[0.06, 8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* Tooltip */}
      {(isActive || hovered) && (
        <Html
          occlude
          position={[0, 0.1, 0]}
          style={{ pointerEvents: 'none' }}
        >
          <div style={{
            width: '190px',
            background: 'var(--color-background-secondary)',
            border: '0.5px solid var(--color-border-tertiary)',
            borderRadius: 'var(--border-radius-md)',
            padding: '8px 10px',
            fontFamily: 'Inter, sans-serif',
            fontSize: '12px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.18)',
            userSelect: 'none',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
              <span style={{ fontWeight: 600, color: 'var(--color-text-primary)', fontSize: '13px' }}>
                {region.name}
              </span>
              <span style={{ fontWeight: 700, color, fontSize: '13px' }}>
                ✦ {region.sci}
              </span>
            </div>
            <div style={{ color: 'var(--color-text-tertiary)', fontSize: '11px', marginBottom: 6 }}>
              {region.sub}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ flex: 1, height: '4px', background: 'var(--color-border-tertiary)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ width: `${barWidth}%`, height: '100%', background: color, borderRadius: 2 }} />
              </div>
              <span style={{ color, fontSize: '10px', fontWeight: 500, whiteSpace: 'nowrap' }}>
                {sciLabel}
              </span>
            </div>
          </div>
        </Html>
      )}
    </group>
  )
}
