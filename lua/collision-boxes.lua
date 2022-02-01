
player_hitbox_color     = 0x60ff2020
player_hurtbox_color    = 0x808000ff
tile_hitbox_color       = 0x80ffff00


Y_OFFSET = 7


player_hitbox_addr      = emu.getLabelAddress("entities_playerHitbox")
player_hurtbox_addr     = emu.getLabelAddress("entities_playerHurtbox")


function draw_u8rect(addr, color)
    local x =      emu.read(addr + 0, emu.memType.cpuDebug)
    local y =      emu.read(addr + 2, emu.memType.cpuDebug)
    local width =  emu.read(addr + 1, emu.memType.cpuDebug) - x
    local height = emu.read(addr + 3, emu.memType.cpuDebug) - y

    if width > 0 and height > 0 then
        emu.drawRectangle(x, y + Y_OFFSET, width, height, color, true)
    end
end



N_ENTITIES = 12

blank_entity_function_addr          = emu.getLabelAddress("entities__blank_entity_function")

entity_process_function_addr        = emu.getLabelAddress("entities_SoA_process_function")
entity_xpos_px_addr                 = emu.getLabelAddress("entities_SoA_xPos") + 1
entity_ypos_px_addr                 = emu.getLabelAddress("entities_SoA_yPos") + 1
entity_tile_hitbox_half_width_addr  = emu.getLabelAddress("entities_SoA_tileHitbox")
entity_tile_hitbox_half_height_addr = entity_tile_hitbox_half_width_addr + 1


function draw_tile_hitboxes()
    for entity_id = 0, N_ENTITIES * 2, 2 do
        local pf = emu.readWord(entity_process_function_addr + entity_id, emu.memType.cpuDebug)
        if pf ~= blank_entity_function_addr then
            local x = emu.read(entity_xpos_px_addr + entity_id, emu.memType.cpuDebug)
            local y = emu.read(entity_ypos_px_addr + entity_id, emu.memType.cpuDebug)
            local half_width = emu.read(entity_tile_hitbox_half_width_addr + entity_id, emu.memType.cpuDebug)
            local half_height = emu.read(entity_tile_hitbox_half_height_addr + entity_id, emu.memType.cpuDebug)

            emu.drawRectangle(x - half_width, y - half_height + Y_OFFSET, half_width * 2, half_height * 2, tile_hitbox_color, true)
        end
    end
end


function onNmi()
    draw_tile_hitboxes()

    draw_u8rect(player_hurtbox_addr, player_hurtbox_color)
    draw_u8rect(player_hitbox_addr,  player_hitbox_color)
end


emu.addEventCallback(onNmi, emu.eventType.nmi)


