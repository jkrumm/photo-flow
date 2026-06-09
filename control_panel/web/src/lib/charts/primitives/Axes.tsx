// Vendored from argo/packages/charts — keep in sync manually.
import { AxisBottom, AxisLeft, AxisRight, type AxisScale } from '@visx/axis'
import { useVxTheme } from '../theme'
import { VX } from '../tokens'
import { fmtAxisDate } from '../utils/format'

type NumericTickFormat = (v: number) => string

export function AxisLeftNumeric({
  scale,
  numTicks = 5,
  tickFormat,
}: {
  scale: AxisScale
  numTicks?: number
  tickFormat?: NumericTickFormat
}) {
  const { axis, axisStroke } = useVxTheme()
  return (
    <AxisLeft
      scale={scale}
      numTicks={numTicks}
      tickFormat={tickFormat as never}
      tickLabelProps={{ fill: axis, fontSize: VX.axisFont, dx: -4 }}
      stroke={axisStroke}
      tickStroke={axisStroke}
    />
  )
}

export function AxisRightNumeric({
  scale,
  left,
  numTicks = 5,
  tickFormat,
}: {
  scale: AxisScale
  left: number
  numTicks?: number
  tickFormat?: NumericTickFormat
}) {
  const { axis, axisStroke } = useVxTheme()
  return (
    <AxisRight
      left={left}
      scale={scale}
      numTicks={numTicks}
      tickFormat={tickFormat as never}
      tickLabelProps={{ fill: axis, fontSize: VX.axisFont, dx: 4 }}
      stroke={axisStroke}
      tickStroke={axisStroke}
    />
  )
}

export function AxisBottomDate({
  scale,
  top,
  tickValues,
}: {
  scale: AxisScale
  top: number
  tickValues: string[]
}) {
  const { axis, axisStroke } = useVxTheme()
  return (
    <AxisBottom
      top={top}
      scale={scale}
      tickValues={tickValues}
      tickFormat={fmtAxisDate}
      tickLabelProps={{ fill: axis, fontSize: VX.axisFont, textAnchor: 'middle' }}
      stroke={axisStroke}
      tickStroke={axisStroke}
    />
  )
}
