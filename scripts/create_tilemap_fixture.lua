local output = app.params["output"]
if output == nil then
  error("missing --script-param output=<path>")
end

local sprite = Sprite(64, 64, ColorMode.RGB)
sprite.gridBounds = Rectangle(0, 0, 32, 32)
app.command.NewLayer {
  name = "Tilemap Verification",
  tilemap = true,
  gridBounds = Rectangle(0, 0, 32, 32),
  ask = false
}

local layer = app.layer
local tileset = layer.tileset
if tileset == nil then
  error("Aseprite did not create a tileset")
end

local tile = sprite:newTile(tileset)
local tileImage = Image(32, 32, ColorMode.RGB)
tileImage:clear(Color { red = 40, green = 180, blue = 90, alpha = 255 })
tile.image = tileImage

local existing = layer:cel(1)
if existing ~= nil then
  sprite:deleteCel(existing)
end
local mapImage = Image(2, 2, ColorMode.TILEMAP)
mapImage:drawPixel(0, 0, app.pixelColor.tile(tile.index))
mapImage:drawPixel(1, 1, app.pixelColor.tile(tile.index))
sprite:newCel(layer, 1, mapImage, Point(0, 0))

local secondTile = sprite:newTile(tileset)
local secondTileImage = Image(32, 32, ColorMode.RGB)
secondTileImage:clear(Color { red = 220, green = 30, blue = 50, alpha = 255 })
secondTile.image = secondTileImage
sprite:newEmptyFrame()
local secondMap = Image(2, 2, ColorMode.TILEMAP)
secondMap:drawPixel(0, 0, app.pixelColor.tile(secondTile.index))
secondMap:drawPixel(1, 0, app.pixelColor.tile(secondTile.index))
secondMap:drawPixel(0, 1, app.pixelColor.tile(secondTile.index))
secondMap:drawPixel(1, 1, app.pixelColor.tile(secondTile.index))
sprite:newCel(layer, 2, secondMap, Point(0, 0))
sprite:saveCopyAs(output)
sprite:close()
