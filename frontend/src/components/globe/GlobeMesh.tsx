import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { shaderMaterial } from '@react-three/drei'
import { extend } from '@react-three/fiber'
import * as topojson from 'topojson-client'
import * as d3 from 'd3-geo'
import type { Topology, GeometryCollection } from 'topojson-specification'

// ── Atmosphere shader ────────────────────────────────────────────────────────

const AtmosphereMaterial = shaderMaterial(
  { uColor: new THREE.Color(0.3, 0.7, 1.0) },
  /* vertex */ `
    varying vec3 vNormal;
    void main() {
      vNormal = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  /* fragment */ `
    uniform vec3 uColor;
    varying vec3 vNormal;
    void main() {
      float intensity = pow(0.65 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.0);
      gl_FragColor = vec4(uColor, 1.0) * intensity;
    }
  `
)

extend({ AtmosphereMaterial })

// Augment JSX namespace so TSX doesn't complain about the custom element
declare module '@react-three/fiber' {
  interface ThreeElements {
    atmosphereMaterial: React.PropsWithChildren<{
      uColor?: THREE.Color
      transparent?: boolean
      blending?: THREE.Blending
      side?: THREE.Side
      depthWrite?: boolean
    }>
  }
}

// ── GlobeMesh ────────────────────────────────────────────────────────────────

export function GlobeMesh() {
  const landMaterialRef = useRef<THREE.MeshBasicMaterial>(null)

  useEffect(() => {
    const canvas = new OffscreenCanvas(2048, 1024)
    const ctx = canvas.getContext('2d')!
    const texture = new THREE.CanvasTexture(canvas as unknown as HTMLCanvasElement)

    // Assign immediately (blank canvas) so the material is wired before fetch
    if (landMaterialRef.current) {
      landMaterialRef.current.map = texture
      landMaterialRef.current.needsUpdate = true
    }

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)

    fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json', {
      signal: controller.signal,
    })
      .then(r => r.json())
      .then((world: Topology<{ countries: GeometryCollection }>) => {
        clearTimeout(timeout)
        const geojson = topojson.feature(world, world.objects.countries)
        const projection = d3.geoEquirectangular().fitSize([2048, 1024], geojson)
        const pathGen = d3.geoPath(projection, ctx)

        ctx.clearRect(0, 0, 2048, 1024)
        ctx.fillStyle = 'rgba(255, 255, 255, 0.90)'
        ctx.beginPath()
        pathGen(geojson)
        ctx.fill()

        // Critical: tell Three.js to re-upload the canvas to the GPU
        texture.needsUpdate = true
        if (landMaterialRef.current) landMaterialRef.current.needsUpdate = true
      })
      .catch(() => {
        clearTimeout(timeout)
        // Fetch failed/timed out — globe renders without land layer, that's fine
      })

    return () => {
      controller.abort()
      clearTimeout(timeout)
      texture.dispose()
    }
  }, [])

  return (
    <group>
      {/* Layer 1 — ocean base */}
      <mesh>
        <sphereGeometry args={[1, 64, 64]} />
        <meshPhongMaterial color="#060F04" specular="#0A2A06" shininess={25} />
      </mesh>

      {/* Layer 2 — land texture (filled after async fetch) */}
      <mesh>
        <sphereGeometry args={[1.002, 64, 64]} />
        <meshBasicMaterial
          ref={landMaterialRef}
          transparent
          depthWrite={false}
        />
      </mesh>

      {/* Layer 3 — atmosphere glow */}
      <mesh>
        <sphereGeometry args={[1.18, 64, 64]} />
          <atmosphereMaterial
          uColor={new THREE.Color(0.3, 0.7, 1.0)}
          transparent
          blending={THREE.AdditiveBlending}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>
    </group>
  )
}
